from datetime import datetime, timezone
import json

import pytest

from harness.channel import DeliveryUncertain, KakaoChannel
from harness.computer_use import ComputerBridge, locate_bridge, migrate_session
from harness.interview import CLOSING, Interview, start_text
from harness.storage import Session
from conftest import Clock, ImmediateExecutor, FakeProvider


def route_for(session):
    config = session.config()
    peer = config.peer_for(session.current()["candidate_id"])
    return {"chat": peer.chat, "author": peer.author, "own_author": config.own_author}


def observation(bridge, rows=(), view=None):
    view = view or bridge.next()
    route, anchor = view["route"], view["anchor"]
    stamp = datetime.fromtimestamp(bridge.now(), timezone.utc).isoformat()
    return {"ticket": view["ticket"], "chat": route["chat"], "own_author": route["own_author"],
            "peer_author": route["author"], "anchor_id": anchor["id"] if anchor else None,
            "anchor": {"author": anchor["author"], "text": anchor["text"], "displayed_at": anchor.get("displayed_at")} if anchor else None,
            "rows": [{"author": who, "text": text, "displayed_at": stamp} for who, text in rows]}


def connected(session, clock):
    bridge = ComputerBridge(session.directory, route_for(session), now=clock)
    bridge.observe(observation(bridge, [("me", "안녕하세요"), ("candidate", "준비됐습니다")]))
    runner = Interview(session, KakaoChannel(session, session.current(), now=clock), FakeProvider(),
                       now=clock, executor=ImmediateExecutor())
    return bridge, runner


def send_on_screen(bridge, clock):
    bridge.observe(observation(bridge))  # Recheck the room immediately before sending.
    request = bridge.next()["action"]
    assert request["status"] == "pending"
    permission = bridge.claim(request["id"])
    clock.advance(1)
    bridge.observe(observation(bridge, [("me", permission["text"])]))
    return permission["text"]


def test_screen_send_is_confirmed_only_after_visible_bubble_and_never_resent(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    with pytest.raises(DeliveryUncertain):
        runner.step()
    assert "started_at" not in session.current()
    request = bridge.next()["action"]
    bridge.claim(request["id"])
    with pytest.raises(ValueError, match="전송"):
        bridge.claim(request["id"])
    clock.advance(1)
    value = observation(bridge, [("me", start_text(session.current()))])
    first = bridge.observe(value)
    assert bridge.observe(value) == first
    assert len(bridge.messages()) == 3
    with pytest.raises(DeliveryUncertain):
        runner.step()  # Intro is now queued.
    assert session.current()["started_at"] == clock() - 1
    assert bridge.next()["action"]["key"] == "intro"
    runner.shutdown()


def test_delayed_start_observation_and_restart_never_extend_deadline(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    with pytest.raises(DeliveryUncertain):
        runner.step()
    bridge.claim(bridge.next()["action"]["id"])
    attempted = clock()
    runner.shutdown()
    clock.advance(1810)
    bridge.observe(observation(bridge, [("me", start_text(session.current()))]))
    resumed = Session(session.directory)
    again = Interview(resumed, KakaoChannel(resumed, resumed.current(), now=clock),
                      now=clock, executor=ImmediateExecutor())
    with pytest.raises(DeliveryUncertain):
        again.step()
    assert resumed.current()["deadline_at"] == attempted + 1800
    assert bridge.next()["action"]["text"] == CLOSING
    send_on_screen(bridge, clock)
    assert again.step() == "feedback"
    assert "늦게" in resumed.current()["protocol_note"]
    assert not any(m["text"].startswith("기술면접에") for m in resumed.current()["messages"])
    again.shutdown()


def test_async_human_question_receipt_releases_next_question(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    for _ in range(2):
        with pytest.raises(DeliveryUncertain):
            runner.step()
        send_on_screen(bridge, clock)
    assert runner.step() == "active"
    with pytest.raises(DeliveryUncertain):
        runner.human_question("본인이 맡은 일은 무엇인가요?")
    send_on_screen(bridge, clock)
    runner.step()
    assert "human_pending" not in session.current()
    with pytest.raises(DeliveryUncertain):
        runner.human_question("그 결과는 어땠나요?")
    assert bridge.next()["action"]["text"] == "그 결과는 어땠나요?"
    runner.shutdown()


def test_anchor_mutation_wrong_room_and_replayed_edit_are_rejected(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    value = observation(bridge)
    value["anchor"]["text"] = "읽지 못한 원문 대신 추정한 말"
    with pytest.raises(ValueError, match="수정·잘림"):
        bridge.observe(value)
    value = observation(bridge)
    value["chat"] = "다른 방"
    with pytest.raises(ValueError, match="실습방"):
        bridge.observe(value)
    value = observation(bridge, [("candidate", "반복 답변"), ("candidate", "반복 답변")])
    result = bridge.observe(value)
    assert len(set(result["recorded_ids"])) == 2
    value["rows"][0]["text"] = "원문 수정"
    with pytest.raises(ValueError, match="바꿀"):
        bridge.observe(value)
    runner.shutdown()


def test_resume_requires_displayed_time_and_excludes_post_deadline_answers(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    for _ in range(2):
        with pytest.raises(DeliveryUncertain):
            runner.step()
        send_on_screen(bridge, clock)
    runner.step()
    clock.value = session.current()["deadline_at"] + 5
    value = observation(bridge, [("candidate", "마감 이후 답변")])
    value["rows"][0].pop("displayed_at")
    with pytest.raises(ValueError, match="분 시각"):
        bridge.observe(value)
    bridge.observe(observation(bridge, [("candidate", "마감 이후 답변")]))
    with pytest.raises(DeliveryUncertain):
        runner.step()
    assert session.current()["late_messages"][-1]["text"] == "마감 이후 답변"
    runner.shutdown()


def test_expired_and_stale_questions_cannot_be_claimed(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    bridge.messages(running=True)
    bridge.enqueue("1:question", "human:1", "시간 지난 질문", clock() + 5)
    clock.advance(6)
    with pytest.raises(ValueError, match="pending"):
        bridge.claim("1:question")
    latest_id = bridge.messages()[-1].message_id
    bridge.enqueue("1:reply", "reply:" + latest_id, "이전 답변에 관한 질문", clock() + 30)
    bridge.observe(observation(bridge, [("candidate", "답변을 정정합니다")]))
    with pytest.raises(ValueError, match="pending"):
        bridge.claim("1:reply")
    assert bridge.next()["action"] is None
    runner.shutdown()


def test_claimed_question_blocks_further_send_until_confirmed_not_sent(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    bridge.messages(running=True)
    bridge.enqueue("1:q", "human:1", "질문", clock() + 5)
    bridge.claim("1:q")
    clock.advance(6)
    bridge.enqueue("1:close", "close", CLOSING, None)
    with pytest.raises(ValueError, match="이전 발송"):
        bridge.claim("1:close")
    bridge.not_sent("1:q", "사용자가 대화방에서 실제 미전송을 확인함")
    assert bridge.next()["action"]["key"] == "close"
    bridge.claim("1:close")
    runner.shutdown()


def test_manual_matching_send_is_not_duplicated_by_desktop(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    with pytest.raises(DeliveryUncertain):
        runner.step()
    clock.advance(1)
    bridge.observe(observation(bridge, [("me", start_text(session.current()))]))
    assert bridge.next()["action"] is None
    with pytest.raises(DeliveryUncertain):
        runner.step()
    assert bridge.next()["action"]["key"] == "intro"
    runner.shutdown()


def test_migration_preserves_clock_original_transcript_and_allocation(session):
    from conftest import runner_for
    clock = Clock()
    runner, _ = runner_for(session, clock)
    runner.step()
    runner.shutdown()
    before = json.loads(json.dumps(session.state))
    config = session.config()
    config.shared_peer = config.peers[session.current()["candidate_id"]]
    config.peers = {}
    config.kakao_backend = "mac"
    (session.directory / "config.json").write_text(config.model_dump_json())
    migrate_session(session.directory, "room-1", "내 표시 이름", "운영 계정")
    migrated = Session(session.directory)
    for key in ("started_at", "deadline_at", "messages", "baseline_ids", "insights"):
        assert migrated.current()[key] == before["interviews"][0][key]
    assert migrated.state["assignment"] == before["assignment"]
    bridge = ComputerBridge(session.directory, route_for(migrated), now=clock)
    bridge.observe(observation(bridge))
    again = Interview(migrated, KakaoChannel(migrated, migrated.current(), now=clock), now=clock,
                      executor=ImmediateExecutor())
    assert again.step() == "active"
    assert migrated.current()["messages"] == before["interviews"][0]["messages"]
    again.shutdown()


def test_setup_commands_wait_without_exposing_authentication(tmp_path):
    bridge, info = locate_bridge(tmp_path / "light")
    assert bridge is None
    assert info["phase"] == "waiting_for_setup"


def test_invalid_observation_blocks_send_until_a_fresh_valid_observation(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    bridge.messages(running=True)
    bridge.enqueue("1:q", "human:1", "질문", clock() + 60)
    bad = observation(bridge)
    bad["anchor"]["text"] = "수정된 원문"
    with pytest.raises(ValueError):
        bridge.observe(bad)
    with pytest.raises(ValueError, match="관찰 검증"):
        bridge.claim("1:q")
    bridge.observe(observation(bridge))
    assert bridge.claim("1:q")["text"] == "질문"
    runner.shutdown()


def test_ambiguous_bubbles_require_explicit_matching_message(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    with pytest.raises(DeliveryUncertain):
        runner.step()
    req = bridge.next()["action"]
    bridge.claim(req["id"])
    clock.advance(1)
    observed = bridge.observe(observation(bridge, [("me", req["text"]), ("me", req["text"])]))
    with pytest.raises(DeliveryUncertain):
        runner.step()
    with pytest.raises(ValueError, match="이미 있습니다"):
        bridge.not_sent(req["id"], "미전송이라고 주장")
    with pytest.raises(ValueError, match="다릅니다"):
        bridge.confirm_sent(req["id"], bridge.messages()[0].message_id, "예전 말풍선")
    bridge.confirm_sent(req["id"], observed["recorded_ids"][0], "실제 첫 발송을 사용자와 확인")
    with pytest.raises(DeliveryUncertain):
        runner.step()  # Next action is intro, not another start.
    assert bridge.next()["action"]["key"] == "intro"
    runner.shutdown()


def test_previous_interview_pending_requests_are_cancelled(session):
    clock = Clock()
    bridge, runner = connected(session, clock)
    bridge.enqueue("1:old", "human:1", "이전 후보 질문", clock() + 100)
    assert bridge.next("2")["action"] is None
    with pytest.raises(ValueError, match="pending"):
        bridge.claim("1:old")
    runner.shutdown()


def test_computer_commands_can_work_while_interview_holds_session_lock(session, capsys):
    from harness import __main__ as cli
    from harness.storage import file_lock
    with file_lock(session.directory / ".lock"):
        assert cli.main(["--session", str(session.directory), "computer-next"]) == 0
        view = json.loads(capsys.readouterr().out)
        bridge = ComputerBridge(session.directory, route_for(session))
        value = observation(bridge, [("me", "안녕하세요"), ("candidate", "준비했습니다")], view=view)
        source = session.directory / "observation-input.json"
        source.write_text(json.dumps(value), encoding="utf-8")
        assert cli.main(["--session", str(session.directory), "computer-observe", "--file", str(source)]) == 0
        assert json.loads(capsys.readouterr().out)["verified"] is True


def test_onboarding_pairing_waits_for_real_screen_bridge(tmp_path, monkeypatch):
    from harness import onboarding
    directory = tmp_path / "light-setup"
    answers = iter(["실습방", "나", "운영 계정", "확인"])
    monkeypatch.setattr(onboarding.platform, "system", lambda: "Darwin")
    def screen_observer(seconds):
        bridge, info = locate_bridge(tmp_path / "light")
        assert info["phase"] == "pairing"
        bridge.observe(observation(bridge, [("나", "안녕하세요"), ("운영 계정", "준비됐습니다")]))
    monkeypatch.setattr(onboarding.time, "sleep", screen_observer)
    config = onboarding.configure(directory, ask=lambda prompt: next(answers))
    assert config.kakao_backend == "computer-use"
    assert config.pairing_verified is True


def test_four_computer_use_interviews_feed_final_payload_without_real_sends(session):
    from conftest import write_feedback
    from harness.finalize import prepare_final
    clock = Clock()
    for number in range(1, 5):
        bridge, runner = connected(session, clock)
        texts = []
        for _ in range(2):
            with pytest.raises(DeliveryUncertain):
                runner.step()
            texts.append(send_on_screen(bridge, clock))
        runner.step()
        clock.advance(130)
        bridge.observe(observation(bridge, [("candidate", "저는 계약 변경을 끝까지 확인했습니다.")]))
        runner.step()
        if number > 1:
            with pytest.raises(DeliveryUncertain):
                runner.step()
            texts.append(send_on_screen(bridge, clock))
            runner.step()
        clock.value = session.current()["deadline_at"] - 120
        with pytest.raises(DeliveryUncertain):
            runner.step()
        texts.append(send_on_screen(bridge, clock))
        runner.step()
        clock.advance(1)
        bridge.observe(observation(bridge, [("candidate", "질문 없습니다. 감사합니다.")]))
        runner.step()
        clock.value = session.current()["deadline_at"]
        with pytest.raises(DeliveryUncertain):
            runner.step()
        texts.append(send_on_screen(bridge, clock))
        assert runner.step() == "feedback"
        runner.shutdown()
        assert texts[0] == start_text(session.current())
        assert texts[-1] == CLOSING
        assert all(m["id"].startswith("cu-observed-") for m in session.current()["messages"])
        write_feedback(session)
        session.accept_feedback("unchanged")
        clock.advance(1)
    prepare_final(session, FakeProvider())
    payload = json.loads((session.directory / "final-payload.json").read_text())
    assert len(payload["ranking"]) == len(payload["interviews"]) == 4
    assert [iv["limit_seconds"] for iv in payload["interviews"]] == [1800, 1200, 1200, 1200]
    assert set(payload["insights"]) == {"markdown"}
