from copy import deepcopy
import json

import httpx
import pytest

from harness.finalize import prepare_final, ranking_context, submit_final, validate_ranking
from harness.models import Ranking
from harness.storage import Session, canonical, sha
from conftest import Clock, FakeProvider, finish_interview, write_feedback


def test_ranking_waits_for_all_four_user_feedbacks(session):
    provider = FakeProvider()
    with pytest.raises(ValueError, match="네 명"):
        prepare_final(session, provider)
    assert provider.calls == []


def test_fourth_feedback_updates_final_standard_for_every_candidate(session):
    clock = Clock()
    for number in range(1, 5):
        finish_interview(session, clock)
        write_feedback(session)
        if number == 4:
            with (session.directory / "insights.md").open("a", encoding="utf-8") as stream:
                stream.write("막힌 상황에서 조기에 공유하는 것을 최우선으로 본다.\n")
        session.accept_feedback("updated" if number == 4 else "unchanged")
        clock.advance(1)
    provider = FakeProvider()
    payload = prepare_final(session, provider)
    assert "최우선" in payload["insights"]["markdown"]
    assert "최우선" in provider.calls[0][1]["final_insights"]
    assert all("최우선" not in iv["insights_used"] for iv in provider.calls[0][1]["candidates"])


def test_complete_submission_uses_final_insights_and_four_candidates(completed, monkeypatch):
    provider = FakeProvider()
    payload = prepare_final(completed, provider)
    assert payload["insights"] == {"markdown": completed.state["final_insights"]}
    assert payload["interviews"][-1]["insights_after_sha256"] == sha(payload["insights"]["markdown"])
    assert len(provider.calls) == 1
    assert provider.calls[0][1]["final_insights"] == completed.state["final_insights"]
    assert [item["candidate_id"] for item in payload["ranking"]] == ["P04", "P03", "P02", "P01"]
    assert payload["payload_sha256"] == sha(canonical({k: v for k, v in payload.items() if k != "payload_sha256"}))
    assert "person@example.test" not in json.dumps(payload, ensure_ascii=False)
    assert "123456" not in json.dumps(payload, ensure_ascii=False)
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["Idempotency-Key"] == payload["submission_id"]
        assert request.headers["X-Participant-Email"] == "person@example.test"
        assert request.headers["X-Participant-Code"] == "123456"
        assert request.headers["X-Participant-Workshop"] == "workshop"
        assert json.loads(request.content) == payload
        return httpx.Response(200, json={"accepted": True, "session_id": payload["session_id"],
                                        "payload_sha256": payload["payload_sha256"]})

    monkeypatch.setenv("TEST_OPERATOR_EMAIL", "person@example.test")
    monkeypatch.setenv("TEST_OPERATOR_CODE", "123456")
    submit_final(completed, httpx.MockTransport(handle))
    assert completed.state["phase"] == "complete"
    assert (completed.directory / "receipt.json").exists()
    submit_final(completed, httpx.MockTransport(handle))
    assert len(calls) == 1


def test_old_frozen_payload_is_not_silently_rehashed_or_sent(completed, monkeypatch):
    payload = prepare_final(completed, FakeProvider())
    payload["insights"]["sha256"] = sha(payload["insights"]["markdown"])
    payload["payload_sha256"] = sha(canonical({k: v for k, v in payload.items() if k != "payload_sha256"}))
    completed.save()
    before = canonical(payload)
    monkeypatch.setenv("TEST_OPERATOR_EMAIL", "person@example.test")
    monkeypatch.setenv("TEST_OPERATOR_CODE", "123456")
    transport = httpx.MockTransport(lambda request: pytest.fail("구계약 전송"))
    with pytest.raises(ValueError, match="이전 insights.sha256"):
        submit_final(completed, transport)
    assert canonical(completed.state["final_payload"]) == before


def test_timeout_retries_identical_payload_and_key_without_reranking(completed, monkeypatch):
    provider = FakeProvider()
    original = prepare_final(completed, provider)
    monkeypatch.setenv("TEST_OPERATOR_EMAIL", "person@example.test")
    monkeypatch.setenv("TEST_OPERATOR_CODE", "123456")
    requests = []

    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("simulated receipt loss", request=request)

    with pytest.raises(ValueError, match="같은 키"):
        submit_final(completed, httpx.MockTransport(timeout))
    assert completed.state["phase"] == "submission_pending"
    (completed.directory / "insights.md").write_text("수정됐지만 이미 확정된 payload에는 영향 없음", encoding="utf-8")
    resumed = Session(completed.directory)
    assert prepare_final(resumed, provider) == original
    assert len(provider.calls) == 1

    def accepted(request):
        requests.append(request)
        return httpx.Response(200, json={"accepted": True, "session_id": original["session_id"],
                                        "payload_sha256": original["payload_sha256"]})

    submit_final(resumed, httpx.MockTransport(accepted))
    assert requests[0].content == requests[1].content
    assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]


def test_missing_endpoint_still_leaves_reviewable_artifacts(completed):
    path = completed.directory / "config.json"
    config = json.loads(path.read_text())
    config["operator"]["url"] = None
    path.write_text(json.dumps(config))
    prepare_final(completed, FakeProvider())
    with pytest.raises(ValueError, match="최종 파일은 준비"):
        submit_final(completed)
    assert (completed.directory / "ranking.md").exists()
    assert (completed.directory / "final-payload.json").exists()


def test_missing_email_code_blocks_http_submission_but_keeps_payload(completed, monkeypatch):
    prepare_final(completed, FakeProvider())
    monkeypatch.delenv("TEST_OPERATOR_EMAIL", raising=False)
    monkeypatch.delenv("TEST_OPERATOR_CODE", raising=False)
    with pytest.raises(ValueError, match="이메일과 6자리"):
        submit_final(completed, httpx.MockTransport(lambda request: pytest.fail("인증 전 전송")))
    assert completed.state["phase"] == "submission_pending"


@pytest.mark.parametrize("fault", ["candidate", "rank", "quote", "source", "interviewer"])
def test_ranking_rejects_wrong_candidates_ranks_or_evidence(completed, fault):
    context, sources = ranking_context(completed)
    ranking = FakeProvider().generate("ranker", context, Ranking)
    bad = deepcopy(ranking)
    first = bad["ranking"][0]
    if fault == "candidate":
        first["candidate_id"] = "P10"
    elif fault == "rank":
        first["rank"] = 2
    elif fault == "quote":
        first["evidence"][0]["quote"] = "실제로 존재하지 않는 인용"
    elif fault == "source":
        first["evidence"][0]["source_id"] = "P01:feedback"
    else:
        first["evidence"][0] = {"source_id": "P04:message:1", "quote": "면접 시작하겠습니다"}
    with pytest.raises(ValueError):
        validate_ranking(bad, ["P01", "P02", "P03", "P04"], sources)


@pytest.mark.parametrize("kind", ["wrong_session", "wrong_hash", "not_accepted", "redirect"])
def test_receipt_must_match_session_hash_and_acceptance(completed, monkeypatch, kind):
    payload = prepare_final(completed, FakeProvider())
    monkeypatch.setenv("TEST_OPERATOR_EMAIL", "person@example.test")
    monkeypatch.setenv("TEST_OPERATOR_CODE", "123456")

    def handler(request):
        receipt = {"accepted": True, "session_id": payload["session_id"], "payload_sha256": payload["payload_sha256"]}
        if kind == "wrong_session":
            receipt["session_id"] = "other"
        elif kind == "wrong_hash":
            receipt["payload_sha256"] = "0" * 64
        elif kind == "not_accepted":
            receipt["accepted"] = False
        return httpx.Response(302 if kind == "redirect" else 200, json=receipt)

    with pytest.raises(ValueError):
        submit_final(completed, httpx.MockTransport(handler))
    assert completed.state["phase"] == "submission_pending"
