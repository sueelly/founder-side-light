"""Behavior-first tests for the approved terminal spec; all dialogue is synthetic."""
from concurrent.futures import Future
from copy import deepcopy
import json

import pytest

from harness.storage import Session, sha


class Clock:
    def __init__(self):
        self.value = 1_800_000_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Immediate:
    def submit(self, fn, *args, **kwargs):
        result = Future()
        try:
            result.set_result(fn(*args, **kwargs))
        except Exception as exc:
            result.set_exception(exc)
        return result

    def shutdown(self, **kwargs):
        pass


class Held(Immediate):
    def __init__(self):
        self.futures = []

    def submit(self, *args, **kwargs):
        result = Future()
        result.set_running_or_notify_cancel()
        self.futures.append(result)
        return result


class Provider:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.cancelled = 0

    def generate(self, role, context, output_type, timeout=90, **kwargs):
        self.calls.append((role, deepcopy(context)))
        if self.fail:
            raise RuntimeError("합성 모델 오류")
        if role == "ranker":
            return {"ranking": [dict(rank=i, candidate_id=c["candidate_id"],
                rationale="사용자 평가에 근거합니다.",
                evidence=[dict(source_id=c["user_feedback"]["source_id"], quote="업무를 끝까지 챙긴다")],
                uncertainty="확인하지 않은 경험이 있습니다.")
                for i, c in enumerate(context["candidates"], 1)]}
        return {"text": "제가 맡은 범위는 목록 정리였습니다." if role == "candidate" else "그때 직접 맡은 범위는 어디까지였나요?"}

    def cancel(self):
        self.cancelled += 1


def make_session(tmp_path):
    s = Session.create(tmp_path / "terminal", model="test", seed=42)
    (s.directory / "insights.md").write_text("# 기준\n\n## 중요하게 보는 기준\n책임감과 협업\n", encoding="utf-8")
    return s


def runner(s, clock, provider=None, executor=None, confirm=True):
    from harness.interview import Interview
    if confirm and s.current()["status"] == "ready":
        s.confirm_insights(sha(s.insights()), at=clock())
    return Interview(s, provider or Provider(), now=clock, monotonic=clock, executor=executor or Immediate())


def feedback(s):
    (s.interview_dir(s.current()) / "feedback.md").write_text(
        "# 합성 테스트 평가\n\n## 종합 평가\n함께 일하고 싶다.\n\n## 근거\n업무를 끝까지 챙긴다.\n\n## 우려·확인할 점\n없음\n", encoding="utf-8")


def finish(s, clock):
    r = runner(s, clock)
    r.step()
    r.step()
    clock.value = s.current()["deadline_at"]
    assert r.step() == "feedback"
    r.shutdown()


def complete(s, clock):
    for _ in range(4):
        finish(s, clock)
        feedback(s)
        s.accept_feedback("unchanged")
        clock.advance(1)
    return s


def test_configuration_free_creation_and_original_assignment(tmp_path):
    s = make_session(tmp_path)
    assert s.state["schema_version"] == "founder-terminal-1"
    assert s.current()["candidate_id"] == "P07"
    assert [i["mode"] for i in s.state["interviews"]] == ["human", "agent", "agent", "agent"]
    assert [i["limit_seconds"] for i in s.state["interviews"]] == [1800, 1200, 1200, 1200]
    assert not (s.directory / "config.json").exists()
    before = (s.directory / "state.json").read_bytes()
    assert Session.create(s.directory, seed=99).state == s.state
    assert (s.directory / "state.json").read_bytes() == before


def test_legacy_session_is_not_mutated(tmp_path):
    p = tmp_path / "old"
    p.mkdir()
    original = '{"schema_version":"founder-light-1","phase":"interviews"}'
    (p / "state.json").write_text(original)
    with pytest.raises(ValueError, match="기존|이전"):
        Session.create(p)
    assert (p / "state.json").read_text() == original


def test_explicit_insights_confirmation_and_changed_content_gate(tmp_path):
    s, c = make_session(tmp_path), Clock()
    r = runner(s, c, confirm=False)
    with pytest.raises(ValueError, match="확인"):
        r.step()
    s.confirm_insights(sha(s.insights()), at=c())
    (s.directory / "insights.md").write_text(s.insights() + "추가 기준\n")
    with pytest.raises(ValueError, match="확인"):
        r.step()
    assert s.current()["messages"] == []
    r.shutdown()


def test_empty_insights_cannot_be_confirmed(tmp_path):
    s = make_session(tmp_path)
    (s.directory / "insights.md").write_text("## 중요하게 보는 기준\n<!-- 비어 있음 -->\n")
    with pytest.raises(ValueError, match="작성"):
        s.confirm_insights(sha(""), at=Clock()())


def test_human_input_and_candidate_are_visible_and_never_auto_answer_for_human(tmp_path):
    s, c, p = make_session(tmp_path), Clock(), Provider()
    r = runner(s, c, p)
    r.step()
    assert [m["speaker"] for m in s.current()["messages"]] == ["interviewer", "interviewer"]
    r.step()
    assert p.calls[0][0] == "candidate"
    for _ in range(4):
        r.step()
    assert len(p.calls) == 1
    r.human_question("직접 맡은 일은 무엇이었나요?")
    r.step(); r.step()
    texts = [m["text"] for m in s.current()["messages"]]
    assert texts.count("직접 맡은 일은 무엇이었나요?") == 1
    assert all(role == "candidate" for role, _ in p.calls)
    document = (s.interview_dir(s.current()) / "transcript.md").read_text()
    assert "직접 맡은 일" in document and "지원자" in document and "면접관" in document
    r.shutdown()


def test_queued_human_input_is_rejected_while_candidate_generates(tmp_path):
    s, c, held = make_session(tmp_path), Clock(), Held()
    r = runner(s, c, executor=held)
    r.step()
    with pytest.raises(ValueError, match="기다|차례"):
        r.human_question("겹치는 질문")
    assert "겹치는 질문" not in json.dumps(s.state, ensure_ascii=False)
    r.shutdown()


def test_ai_roles_alternate_and_reject_human_injection(tmp_path):
    s, c = make_session(tmp_path), Clock()
    finish(s, c); feedback(s); s.accept_feedback("unchanged")
    p = Provider()
    r = runner(s, c, p)
    for _ in range(5):
        r.step()
    assert [role for role, _ in p.calls[:4]] == ["candidate", "interviewer", "candidate", "interviewer"]
    with pytest.raises(ValueError, match="사람"):
        r.human_question("대신 질문")
    r.shutdown()


def test_role_contexts_do_not_leak_criteria_persona_or_assignment(tmp_path):
    from harness.candidate import candidate_context
    s, c = make_session(tmp_path), Clock()
    s.state["assignment"]["secret"] = "POOL_SENTINEL"
    r = runner(s, c)
    r.step()
    candidate = candidate_context(s, s.current())
    interviewer = r.context()
    assert set(candidate) == {"persona", "company_public", "scene", "transcript", "closing_stage"}
    assert "persona" in candidate and "persona" not in interviewer
    assert "책임감과 협업" not in json.dumps(candidate, ensure_ascii=False)
    assert "POOL_SENTINEL" not in json.dumps([candidate, interviewer])
    assert "future_relevance" not in json.dumps(candidate)
    assert "evidence_refs" not in json.dumps(candidate)
    r.shutdown()


def test_deadline_cancels_generation_and_discards_late_result(tmp_path):
    s, c, held, p = make_session(tmp_path), Clock(), Held(), Provider()
    r = runner(s, c, p, held)
    r.step()
    pending = held.futures[0]
    c.value = s.current()["deadline_at"]
    assert r.step() == "feedback"
    pending.set_result({"text": "마감 뒤 결과"})
    assert r.step() == "feedback"
    assert p.cancelled > 0
    assert [m["text"] for m in s.current()["messages"]][-1] == "수고하셨습니다"
    assert "마감 뒤 결과" not in json.dumps(s.state, ensure_ascii=False)
    r.shutdown()


def test_resume_after_deadline_closes_once_without_extending(tmp_path):
    s, c = make_session(tmp_path), Clock()
    r = runner(s, c)
    r.step(); r.step()
    original_deadline = s.current()["deadline_at"]
    r.shutdown()
    c.advance(1810)
    resumed = Session(s.directory)
    p = Provider()
    again = runner(resumed, c, p, confirm=False)
    assert again.step() == "feedback"
    assert resumed.current()["deadline_at"] == original_deadline
    texts = [m["text"] for m in resumed.current()["messages"]]
    assert texts.count("수고하셨습니다") == 1
    assert len([t for t in texts if t.endswith("님 면접 시작하겠습니다")]) == 1
    assert resumed.current()["protocol_note"]
    assert not p.calls
    again.shutdown()


def test_wrap_waits_for_pending_candidate_but_no_new_core_question(tmp_path):
    from harness.interview import LAST_QUESTION
    s, c, held = make_session(tmp_path), Clock(), Held()
    r = runner(s, c, executor=held)
    r.step()
    c.value = s.current()["deadline_at"] - 120
    r.step()
    assert LAST_QUESTION not in [m["text"] for m in s.current()["messages"]]
    held.futures[0].set_result({"text": "경계 전에 요청한 답입니다."})
    r.step()
    texts = [m["text"] for m in s.current()["messages"]]
    assert texts.index("경계 전에 요청한 답입니다.") < texts.index(LAST_QUESTION)
    r.shutdown()


def test_failed_generation_requires_retry_and_never_duplicates_turn(tmp_path):
    s, c, p = make_session(tmp_path), Clock(), Provider()
    p.fail = True
    r = runner(s, c, p)
    r.step(); r.step()
    for _ in range(3): r.step()
    assert len(p.calls) == 1
    p.fail = False
    r.retry_generation()
    r.step(); r.step()
    assert len([m for m in s.current()["messages"] if m["speaker"] == "candidate"]) == 1
    r.shutdown()
    again = runner(Session(s.directory), c, p, confirm=False)
    again.step()
    assert len(p.calls) == 2
    again.shutdown()


def test_human_early_end_needs_wrap_reply(tmp_path):
    s, c = make_session(tmp_path), Clock()
    r = runner(s, c)
    r.step(); r.step()
    with pytest.raises(ValueError): r.request_end()
    r.request_last(); r.step()
    with pytest.raises(ValueError): r.request_end()
    r.step()
    r.request_end()
    assert r.step() == "feedback"
    r.shutdown()


def test_feedback_and_insight_choice_gate_all_four(tmp_path):
    s, c = make_session(tmp_path), Clock()
    for i in range(4):
        assert s.current()["number"] == i + 1
        finish(s, c)
        with pytest.raises(ValueError, match="작성"):
            s.accept_feedback("unchanged")
        with pytest.raises(ValueError, match="평가"):
            runner(s, c)
        feedback(s)
        if i == 3:
            (s.directory / "insights.md").write_text(s.insights() + "조기 공유도 본다.\n")
            with pytest.raises(ValueError, match="실제 변경"):
                s.accept_feedback("unchanged")
        s.accept_feedback("updated" if i == 3 else "unchanged")
    assert s.state["phase"] == "finalizing"
    assert "조기 공유" in s.state["final_insights"]


def test_snapshot_stays_fixed_during_interview(tmp_path):
    s, c = make_session(tmp_path), Clock()
    r = runner(s, c); r.step()
    before = r.context()["insights"]
    (s.directory / "insights.md").write_text(s.insights() + "수정 기준\n")
    assert r.context()["insights"] == before
    r.shutdown()


def test_modified_transcript_stops_before_new_generation(tmp_path):
    s, c, p = make_session(tmp_path), Clock(), Provider()
    r = runner(s, c, p); r.step(); r.step()
    (s.interview_dir(s.current()) / "transcript.md").write_text("외부 변경")
    with pytest.raises(ValueError, match="변경|손상"):
        r.human_question("새 질문")
    assert len(p.calls) == 1
    r.shutdown()


def test_missing_output_after_commit_is_regenerated_not_rerun(tmp_path, monkeypatch):
    import harness.storage as storage
    s, c = make_session(tmp_path), Clock()
    actual = storage.atomic_write
    def fail_md(path, text, **kwargs):
        if path.name == "transcript.md":
            raise OSError("합성 출력 실패")
        return actual(path, text, **kwargs)
    monkeypatch.setattr(storage, "atomic_write", fail_md)
    r = runner(s, c)
    with pytest.raises(OSError): r.step()
    monkeypatch.setattr(storage, "atomic_write", actual)
    r.shutdown()
    resumed = Session(s.directory)
    assert (resumed.interview_dir(resumed.current()) / "transcript.md").exists()
    assert len([m for m in resumed.current()["messages"] if m["text"].endswith("님 면접 시작하겠습니다")]) == 1


def test_ranker_is_local_idempotent_and_has_no_private_inputs(tmp_path, monkeypatch):
    import socket
    from harness.finalize import prepare_final
    s = complete(make_session(tmp_path), Clock())
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("운영 서버 연결"))
    p = Provider()
    result = prepare_final(s, p)
    assert s.state["phase"] == "complete"
    assert len(result["ranking"]) == 4
    assert result["insights_sha256"] == s.state["interviews"][-1]["insights_after_sha256"]
    assert not (s.directory / "final-payload.json").exists()
    assert not (s.directory / "receipt.json").exists()
    assert (s.directory / "ranking.md").exists()
    assert (s.directory / "ranking.json").exists()
    (s.directory / "insights.md").write_text("나중의 변경")
    assert prepare_final(Session(s.directory), p) == result
    assert len(p.calls) == 1
    assert "persona" not in json.dumps(p.calls)


@pytest.mark.parametrize("fault", ["candidate", "rank", "quote", "source", "interviewer"])
def test_ranking_rejects_invalid_evidence(tmp_path, fault):
    from harness.finalize import ranking_context, validate_ranking
    from harness.models import Ranking
    s = complete(make_session(tmp_path), Clock())
    context, sources = ranking_context(s)
    result = Provider().generate("ranker", context, Ranking)
    item = result["ranking"][0]
    if fault == "candidate": item["candidate_id"] = "P09"
    elif fault == "rank": item["rank"] = 2
    elif fault == "quote": item["evidence"][0]["quote"] = "존재하지 않는 인용"
    elif fault == "source": item["evidence"][0]["source_id"] = context["candidates"][1]["user_feedback"]["source_id"]
    else:
        first = context["candidates"][0]["messages"][0]
        item["evidence"][0] = {"source_id": first["source_id"], "quote": first["text"]}
    with pytest.raises(ValueError):
        validate_ranking(result, [i["candidate_id"] for i in s.state["interviews"]], sources)


def test_candidates_cover_every_confirmed_pool_and_have_no_future_fields():
    from harness.candidate import load_persona, validate_bundle
    from harness.allocation import load_pools
    ids = {cid for group in load_pools().values() for cid in group}
    assert set(validate_bundle()) == ids
    for cid in ids:
        value = load_persona(cid)
        assert value["id"] == cid
        assert value["history"]
        for key in ("future_relevance", "evidence_refs", "trajectory", "tenure_months", "simulation_drivers"):
            assert key not in json.dumps(value)


def test_first_init_needs_no_config_and_auth_failure_creates_nothing(tmp_path, monkeypatch):
    from harness import __main__ as cli
    monkeypatch.setattr(cli, "ensure_claude_login", lambda: (_ for _ in ()).throw(ValueError("로그인 필요")))
    path = tmp_path / "init"
    assert cli.main(["--session", str(path), "init"]) == 1
    assert not path.exists()
    monkeypatch.setattr(cli, "ensure_claude_login", lambda: None)
    assert cli.main(["--session", str(path), "init"]) == 0
    before = (path / "state.json").read_bytes()
    assert cli.main(["--session", str(path), "init"]) == 0
    assert before == (path / "state.json").read_bytes()
