import pytest

from conftest import Clock, runner_for


def test_human_can_type_questions_in_terminal_and_deadline_is_enforced(session):
    clock = Clock()
    runner, cli = runner_for(session, clock)
    runner.step()
    runner.human_question("직접 맡은 일은 무엇인가요?")
    assert cli.sent[-1][1] == "직접 맡은 일은 무엇인가요?"
    clock.value = session.current()["deadline_at"]
    with pytest.raises(TimeoutError):
        runner.human_question("너무 늦은 질문")
    runner.shutdown()


def test_delayed_closing_has_receiver_required_protocol_note(session):
    clock = Clock()
    runner, _ = runner_for(session, clock)
    runner.step()
    clock.value = session.current()["deadline_at"] + 3
    assert runner.step() == "feedback"
    assert "3.0초" in session.current()["protocol_note"]
    runner.shutdown()
