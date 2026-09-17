from concurrent.futures import Future

import pytest

from harness.channel import DeliveryUncertain, KakaoChannel, resolve_not_sent, resolve_sent, timestamp
from harness.interview import CLOSING, LAST_QUESTION, Interview, start_text
from harness.storage import Session
from conftest import Clock, FakeCli, FakeProvider, ImmediateExecutor, finish_interview, runner_for, write_feedback


def test_full_four_interview_journey_requires_feedback(session):
    clock = Clock()
    for number in range(1, 5):
        iv = session.current()
        assert iv["number"] == number
        assert iv["mode"] == ("human" if number == 1 else "agent")
        cli = finish_interview(session, clock)
        texts = [text for _, text in cli.sent]
        assert texts[0] == start_text(iv)
        assert "자기소개" in texts[1]
        assert LAST_QUESTION in texts
        assert texts[-1] == CLOSING
        assert iv["deadline_at"] - iv["started_at"] == (1800 if number == 1 else 1200)
        with pytest.raises(ValueError, match="사용자가 작성"):
            session.accept_feedback("unchanged")
        with pytest.raises(ValueError, match="평가"):
            runner_for(session, clock)
        write_feedback(session)
        session.accept_feedback("unchanged")
        clock.advance(1)
    assert session.current() is None
    assert session.state["phase"] == "finalizing"
    with pytest.raises(ValueError, match="평가만"):
        session.accept_feedback("unchanged")


def test_human_early_end_requires_last_invitation_and_answer(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    runner.step()
    with pytest.raises(ValueError, match="먼저 last"):
        runner.request_end()
    runner.request_last()
    runner.step()
    with pytest.raises(ValueError, match="기다리세요"):
        runner.request_end()
    clock.advance(1)
    cli.add("마지막으로 팀과 잘 협업하고 싶습니다.")
    runner.step()
    runner.request_end()
    assert runner.step() == "feedback"


def test_human_deadline_closes_even_without_candidate_answer(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    runner.step()
    clock.advance(1680)
    runner.step()
    assert cli.sent[-1][1] == LAST_QUESTION
    clock.advance(120)
    assert runner.step() == "feedback"
    assert cli.sent[-1][1] == CLOSING


def test_resume_uses_actual_start_and_does_not_extend_deadline(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    runner.step()
    start = session.current()["started_at"]
    clock.advance(1810)
    resumed = Session(session.directory)
    channel = KakaoChannel(resumed, resumed.current(), cli, clock)
    again = Interview(resumed, channel, now=clock, executor=ImmediateExecutor())
    assert again.step() == "feedback"
    assert resumed.current()["started_at"] == start
    assert resumed.current()["deadline_at"] == start + 1800
    assert [text for _, text in cli.sent].count(start_text(resumed.current())) == 1
    assert "protocol_note" in resumed.current()


def test_uncertain_start_is_reconciled_without_duplicate_send(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    cli.fail_after_send = True
    with pytest.raises(DeliveryUncertain):
        runner.step()
    assert "started_at" not in session.current()
    clock.advance(10)
    runner.step()
    assert session.current()["started_at"] == clock() - 10
    assert len([text for _, text in cli.sent if text == start_text(session.current())]) == 1


def test_no_receipt_never_silently_resends(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    cli.hide = True
    for _ in range(3):
        with pytest.raises(DeliveryUncertain):
            runner.step()
    assert len(cli.sent) == 1
    assert "started_at" not in session.current()


def test_manual_sent_reconciliation_checks_the_actual_channel_message(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    cli.fail_after_send = True
    with pytest.raises(DeliveryUncertain):
        runner.step()
    candidate_echo = cli.add(start_text(session.current()), "candidate")
    with pytest.raises(ValueError, match="일치하지"):
        resolve_sent(session, "start", candidate_echo.message_id, "후보가 되말한 메시지", cli=cli)
    resolve_sent(session, "start", "1", "대화방에서 내 시작 메시지 확인", cli=cli)
    runner.step()
    assert [text for _, text in cli.sent].count(start_text(session.current())) == 1


def test_only_explicit_not_sent_resolution_allows_retry(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    cli.hide = True
    with pytest.raises(DeliveryUncertain):
        runner.step()
    with pytest.raises(ValueError, match="근거"):
        resolve_not_sent(session, "start", "")
    # Simulate the human seeing no delivery, rather than blindly retrying a timeout.
    cli.items.clear()
    resolve_not_sent(session, "start", "대화방에 메시지가 없는 것을 직접 확인")
    cli.hide = False
    runner.step()
    assert len([text for _, text in cli.sent if text == start_text(session.current())]) == 2


def test_candidate_echo_and_old_opening_do_not_start_clock(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    cli.add(start_text(session.current()), "me", at=clock() - 10)
    cli.add(start_text(session.current()), "candidate")
    runner.step()
    assert session.current()["started_at"] == clock()
    assert all(message["id"] not in {"1", "2"} for message in session.current()["messages"])


class HeldExecutor(ImmediateExecutor):
    def __init__(self):
        self.future = Future()

    def submit(self, *args, **kwargs):
        return self.future


def advance_to_agent(session, clock):
    finish_interview(session, clock)
    write_feedback(session)
    session.accept_feedback("unchanged")


def test_blocked_model_cannot_block_wrap_or_deadline(session):
    clock = Clock()
    advance_to_agent(session, clock)
    executor = HeldExecutor()
    runner, cli = runner_for(session, clock, executor=executor)
    runner.step()
    clock.advance(5)
    cli.add("저는 프로젝트 운영을 담당했습니다.")
    runner.step()
    assert runner.future is executor.future
    clock.value = session.current()["deadline_at"] - 120
    runner.step()
    assert cli.sent[-1][1] == LAST_QUESTION
    executor.future.set_result({"text": "이미 기한을 넘긴 새 질문"})
    runner.step()
    assert all("기한을 넘긴" not in text for _, text in cli.sent)
    clock.value = session.current()["deadline_at"]
    cli.add("마감 후에 온 답변입니다.")
    assert runner.step() == "feedback"
    assert cli.sent[-1][1] == CLOSING
    assert session.current()["late_messages"][-1]["text"] == "마감 후에 온 답변입니다."


def test_new_candidate_message_invalidates_stale_model_draft(session):
    clock = Clock()
    advance_to_agent(session, clock)
    executor = HeldExecutor()
    runner, cli = runner_for(session, clock, executor=executor)
    runner.step()
    clock.advance(1)
    cli.add("처음에는 제가 맡았다고 말했습니다.")
    runner.step()
    clock.advance(1)
    cli.add("정정할게요. 그 부분은 동료가 맡았습니다.")
    executor.future.set_result({"text": "방금 정정에 맞지 않는 옛 질문"})
    runner.step()
    assert all("옛 질문" not in text for _, text in cli.sent)


def test_insight_snapshot_stays_stable_and_feedback_action_is_checked(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    runner.step()
    before = session.current()["insights"]
    path = session.directory / "insights.md"
    path.write_text(before + "정직함도 본다.\n", encoding="utf-8")
    assert runner.context()["insights"] == before
    clock.value = session.current()["deadline_at"]
    runner.step()
    write_feedback(session)
    with pytest.raises(ValueError, match="실제 변경"):
        session.accept_feedback("unchanged")
    iv = session.accept_feedback("updated")
    assert iv["insights"] == before
    assert "정직함" in iv["insights_after"]
    next_runner, _ = runner_for(session, clock)
    next_runner.step()
    assert "정직함" in session.current()["insights"]


def test_empty_initial_insights_blocks_actual_send(session):
    (session.directory / "insights.md").write_text("## 중요하게 보는 기준\n<!-- 나중에 작성 -->\n", encoding="utf-8")
    runner, cli = runner_for(session, Clock())
    with pytest.raises(ValueError, match="사용자가 작성"):
        runner.step()
    assert not cli.sent


def test_started_route_cannot_be_swapped(session):
    import json
    runner, _ = runner_for(session, Clock())
    runner.step()
    path = session.directory / "config.json"
    data = json.loads(path.read_text())
    data["peers"]["P01"]["chat"] = "different-room"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="바꿀"):
        runner_for(session, Clock())


@pytest.mark.parametrize("value", [None, "2026-09-17T12:00:00", "nan", "-1", "inf"])
def test_timestamp_rejects_missing_naive_or_nonfinite(value):
    with pytest.raises(ValueError):
        timestamp(value)


def test_timestamp_accepts_timezone():
    assert timestamp("2026-09-17T12:00:00+09:00") == timestamp("2026-09-17T03:00:00Z")
