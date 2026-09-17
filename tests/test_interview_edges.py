"""Synthetic boundary and recovery regressions; no participant dialogue or evaluation."""
import json

import pytest

from harness.storage import Session
from test_terminal_contract import Clock, Held, Provider, make_session, runner


def test_resume_detects_clock_rollback_since_last_idle_poll(tmp_path):
    """Time observed while a generation stalls must survive process restart."""
    session, clock = make_session(tmp_path), Clock()
    original = runner(session, clock, executor=Held())
    original.step()
    clock.advance(600)
    original.step()
    remaining_before_shutdown = original.remaining()
    original.shutdown()

    # The OS clock is moved back during restart, but still after the last
    # utterance. A message-only high-water mark silently grants extra time.
    clock.advance(-300)
    resumed = Session(session.directory)
    active = runner(resumed, clock, executor=Held(), confirm=False)
    try:
        with pytest.raises(ValueError, match="시계"):
            active.step()
        assert active.remaining() <= remaining_before_shutdown
    finally:
        active.shutdown()


def test_deadline_crossed_between_result_validation_and_append_discards_reply(tmp_path, monkeypatch):
    """A pause just before append must not make a post-deadline answer official."""
    session, clock, pending = make_session(tmp_path), Clock(), Held()
    active = runner(session, clock, executor=pending)
    active.step()
    deadline = session.current()["deadline_at"]
    clock.value = deadline - 1
    pending.futures[0].set_result({"text": "마감 뒤에 확정되면 안 되는 답입니다."})
    actual_append = active._append

    def delayed_append(key, *args, **kwargs):
        if key.startswith("reply:"):
            clock.value = deadline + 1
        return actual_append(key, *args, **kwargs)

    monkeypatch.setattr(active, "_append", delayed_append)
    try:
        assert active.step() == "feedback"
        disk = json.loads((session.directory / "state.json").read_text())
        messages = disk["interviews"][0]["messages"]
        assert not [m for m in messages if m["speaker"] == "candidate"]
        assert messages[-1]["text"] == "수고하셨습니다"
    finally:
        active.shutdown()


def test_deadline_crossed_while_accepting_human_input_does_not_record_it(tmp_path, monkeypatch):
    session, clock = make_session(tmp_path), Clock()
    active = runner(session, clock)
    active.step()
    active.step()
    deadline = session.current()["deadline_at"]
    clock.value = deadline - 1
    actual_append = active._append

    def delayed_append(key, *args, **kwargs):
        if key.startswith("human:"):
            clock.value = deadline + 1
        return actual_append(key, *args, **kwargs)

    monkeypatch.setattr(active, "_append", delayed_append)
    try:
        try:
            active.human_question("늦게 접수된 질문")
        except ValueError:
            pass  # Either explicit refusal or normal deadline closure is safe.
        assert active.step() == "feedback"
        disk = json.loads((session.directory / "state.json").read_text())
        assert "늦게 접수된 질문" not in json.dumps(disk, ensure_ascii=False)
    finally:
        active.shutdown()


def test_deadline_crossed_during_state_write_discards_uncommitted_speech(tmp_path, monkeypatch):
    from harness import storage
    session, clock, pending = make_session(tmp_path), Clock(), Held()
    active = runner(session, clock, executor=pending)
    active.step()
    deadline = session.current()["deadline_at"]
    clock.value = deadline - 1
    pending.futures[0].set_result({"text": "저장 도중 마감된 답변"})
    actual_write = storage.atomic_write

    def delayed_write(path, text, **kwargs):
        if path.name == "state.json" and "저장 도중 마감된 답변" in text:
            clock.value = deadline + 1
        return actual_write(path, text, **kwargs)

    monkeypatch.setattr(storage, "atomic_write", delayed_write)
    try:
        assert active.step() == "feedback"
        restored = Session(session.directory)
        assert "저장 도중 마감된 답변" not in json.dumps(restored.state, ensure_ascii=False)
        assert restored.current()["messages"][-1]["text"] == "수고하셨습니다"
    finally:
        active.shutdown()


def test_long_pause_during_start_closes_without_starting_generation(tmp_path, monkeypatch):
    from harness import storage
    session, clock, provider = make_session(tmp_path), Clock(), Provider()
    active = runner(session, clock, provider)
    actual_write = storage.atomic_write
    paused = False

    def delayed_start(path, text, **kwargs):
        nonlocal paused
        if not paused and path.name == "state.json" and '"started_at"' in text:
            paused = True
            clock.advance(1801)
        return actual_write(path, text, **kwargs)

    monkeypatch.setattr(storage, "atomic_write", delayed_start)
    try:
        assert active.step() == "feedback"
        restored = Session(session.directory)
        assert [m["key"] for m in restored.current()["messages"]] == ["start", "close"]
        assert not provider.calls
    finally:
        active.shutdown()


def test_status_does_not_show_extra_time_after_clock_rollback(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from harness import __main__ as cli
    session, clock = make_session(tmp_path), Clock()
    active = runner(session, clock, executor=Held())
    try:
        active.step()
        clock.advance(600)
        active.step()
        monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda: clock() - 300))
        cli.print_status(session)
        assert "남은 시간: 1200초" in capsys.readouterr().out
    finally:
        active.shutdown()
