"""Synthetic terminal contracts. Actual IME/resize checks remain OS-specific."""
import asyncio
from types import SimpleNamespace

import pytest

from harness import terminal


class Runner:
    def __init__(self, mode="human"):
        self.iv = {"number": 1, "name": "테스트", "mode": mode, "status": "active", "messages": []}
        self.last_error = None
        self.calls = []
        self.busy = False
        self.closed = False

    def human_question(self, text):
        if self.busy:
            raise ValueError("지원자 응답 중에는 새 발화를 받을 수 없습니다")
        self.calls.append(("speech", text))

    def request_last(self):
        self.calls.append("last")

    def request_end(self):
        self.calls.append("end")

    def retry_generation(self):
        self.calls.append("retry")

    def remaining(self):
        return 125

    def waiting_label(self):
        return "지원자 답변 생성 중…" if self.busy else "면접관 입력 대기"

    def shutdown(self):
        self.closed = True


def test_plain_korean_and_q_alias_are_speech_but_commands_are_not():
    runner = Runner()
    output = []
    for value in ("갈등 경험을 말씀해 주세요.", "q  구체적인 상황은요?", "/last", "/end", "/retry", "/status"):
        terminal.handle_input(runner, value, output=output.append)
    assert runner.calls == [("speech", "갈등 경험을 말씀해 주세요."), ("speech", "구체적인 상황은요?"), "last", "end", "retry"]
    assert "02:05" in output[0]


@pytest.mark.parametrize("value", ["질문", "/last", "/end"])
def test_ai_interview_rejects_human_speech_and_control(value):
    runner = Runner("agent")
    with pytest.raises(ValueError):
        terminal.handle_input(runner, value)
    assert runner.calls == []


def test_candidate_busy_rejects_without_queuing_and_preserves_draft():
    runner = Runner()
    runner.busy = True
    output = []
    assert terminal.submit_input(runner, "아직 쓰던 한국어 질문", output=output.append) == "아직 쓰던 한국어 질문"
    assert runner.calls == []
    runner.busy = False
    assert runner.calls == []  # The rejected input must never be sent automatically.
    assert output and "접수되지 않았습니다" in output[0]


def test_timer_role_and_speaker_labels():
    runner = Runner()
    runner.busy = True
    assert "02:05" in terminal.status_line(runner)
    assert "지원자 답변 생성 중" in terminal.status_line(runner)
    assert terminal.message_line(runner.iv, {"speaker": "candidate", "text": "네."}) == "지원자(테스트): 네."
    assert terminal.message_line(runner.iv, {"speaker": "interviewer", "text": "소개해주세요."}) == "면접관: 소개해주세요."


def test_async_model_output_does_not_wait_for_enter_and_shutdown_cancels_prompt():
    runner = Runner()
    ticks = 0
    cancelled = []
    output = []

    def step():
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            runner.iv["messages"].append({"id": "m1", "speaker": "candidate", "text": "실시간 답변"})
        return "feedback" if ticks >= 4 else "active"
    runner.step = step

    class PendingPrompt:
        app = SimpleNamespace(invalidate=lambda: None)
        async def prompt_async(self, *args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
    asyncio.run(terminal.run_terminal(runner, prompt=PendingPrompt(), output=output.append, poll_interval=0))
    assert output.count("지원자(테스트): 실시간 답변") == 1
    assert cancelled and runner.closed


def test_enter_during_candidate_turn_is_rejected_before_consuming_model_reply():
    runner = Runner()
    runner.busy = True
    tick = 0
    defaults = []
    output = []
    def step():
        nonlocal tick
        tick += 1
        if tick >= 2:
            runner.busy = False
        return "feedback" if tick >= 4 else "active"
    runner.step = step
    class Prompt:
        app = SimpleNamespace(invalidate=lambda: None)
        async def prompt_async(self, *args, **kwargs):
            defaults.append(kwargs["default"])
            if len(defaults) == 1:
                return "지원자 답변 중 작성한 질문"
            await asyncio.Future()
    asyncio.run(terminal.run_terminal(runner, prompt=Prompt(), output=output.append, poll_interval=0))
    assert runner.calls == []
    assert defaults == ["", "지원자 답변 중 작성한 질문"]


def test_eof_stops_runner_without_waiting_for_model():
    runner = Runner()
    runner.step = lambda: "active"
    class Prompt:
        app = SimpleNamespace(invalidate=lambda: None)
        async def prompt_async(self, *args, **kwargs):
            raise EOFError()
    with pytest.raises(EOFError):
        asyncio.run(terminal.run_terminal(runner, prompt=Prompt(), poll_interval=0))
    assert runner.closed


def test_ctrl_c_cleans_up_without_unhandled_prompt_task():
    runner = Runner()
    runner.step = lambda: "active"
    class Prompt:
        app = SimpleNamespace(invalidate=lambda: None)
        async def prompt_async(self, *args, **kwargs):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(terminal.run_terminal(runner, prompt=Prompt(), poll_interval=0))
    assert runner.closed


async def _until(condition, timeout=3):
    async def wait():
        while not condition():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout=timeout)


def test_installed_prompt_toolkit_keeps_korean_draft_during_live_output():
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    runner = Runner()
    runner.step = lambda: "feedback" if runner.calls else "active"
    output = []
    with create_pipe_input() as pipe:
        prompt = PromptSession(input=pipe, output=DummyOutput())
        async def drive():
            await _until(lambda: prompt.app.is_running)
            pipe.send_text("제가 맡은 업무")
            await _until(lambda: prompt.default_buffer.text == "제가 맡은 업무")
            runner.iv["messages"].append({"id": "live-1", "speaker": "candidate", "text": "완료된 실시간 답변"})
            await _until(lambda: "지원자(테스트): 완료된 실시간 답변" in output)
            assert prompt.default_buffer.text == "제가 맡은 업무"
            # Bracketed paste is handled by the actual installed input stack.
            pipe.send_text("\x1b[200~에 대해 질문합니다.\x1b[201~")
            await _until(lambda: prompt.default_buffer.text == "제가 맡은 업무에 대해 질문합니다.")
            pipe.send_text("\r")

        async def journey():
            controller = asyncio.create_task(drive())
            try:
                await asyncio.wait_for(terminal.run_terminal(
                    runner, prompt=prompt, output=output.append, poll_interval=0.005), timeout=4)
                await controller
            finally:
                controller.cancel()
        asyncio.run(journey())
    assert runner.calls == [("speech", "제가 맡은 업무에 대해 질문합니다.")]
    assert output.count("지원자(테스트): 완료된 실시간 답변") == 1
    assert runner.closed


def test_actual_interview_with_pipe_input_rejects_busy_then_accepts_explicit_resubmission(session):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from test_terminal_contract import Clock, Held, Provider, runner as make_runner
    clock, executor, provider = Clock(), Held(), Provider()
    interview = make_runner(session, clock, provider=provider, executor=executor)
    output = []
    question = "본인이 담당한 범위를 알려주세요."
    with create_pipe_input() as pipe:
        prompt = PromptSession(input=pipe, output=DummyOutput())
        async def drive():
            await _until(lambda: prompt.app.is_running and len(executor.futures) == 1)
            pipe.send_text(question + "\r")
            await _until(lambda: any("접수되지 않았습니다" in line for line in output))
            await _until(lambda: prompt.app.is_running and prompt.default_buffer.text == question)
            assert len(interview.iv["messages"]) == 2
            executor.futures[0].set_result({"text": "제가 맡았던 일은 목록을 정리하는 것이었습니다."})
            await _until(lambda: interview.iv["next_role"] == "human")
            assert len(interview.iv["messages"]) == 3
            assert prompt.default_buffer.text == question
            # Waiting for the candidate did not automatically submit the draft.
            pipe.send_text("\r")
            await _until(lambda: len(executor.futures) == 2)
            assert interview.iv["messages"][-1]["text"] == question
            clock.value = interview.iv["deadline_at"]

        async def journey():
            controller = asyncio.create_task(drive())
            try:
                await asyncio.wait_for(terminal.run_terminal(
                    interview, prompt=prompt, output=output.append, poll_interval=0.005), timeout=4)
                await controller
            finally:
                controller.cancel()
                interview.shutdown()
        asyncio.run(journey())
    assert interview.iv["status"] == "feedback"
    assert interview.iv["messages"][-1]["text"] == "수고하셨습니다"
    assert provider.cancelled == 1
    assert output[-1] == "면접관: 수고하셨습니다"
    transcript = (session.interview_dir(interview.iv) / "transcript.md").read_text(encoding="utf-8")
    assert transcript.count(question) == 1


def test_installed_prompt_toolkit_ctrl_c_cleans_up_pending_interview(session):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from test_terminal_contract import Clock, Held, Provider, runner as make_runner
    clock, executor, provider = Clock(), Held(), Provider()
    interview = make_runner(session, clock, provider=provider, executor=executor)
    with create_pipe_input() as pipe:
        prompt = PromptSession(input=pipe, output=DummyOutput())
        async def drive():
            await _until(lambda: prompt.app.is_running and len(executor.futures) == 1)
            pipe.send_text("아직 입력 중인 문장")
            await _until(lambda: prompt.default_buffer.text == "아직 입력 중인 문장")
            pipe.send_bytes(b"\x03")

        async def journey():
            controller = asyncio.create_task(drive())
            try:
                await asyncio.wait_for(terminal.run_terminal(
                    interview, prompt=prompt, output=lambda line: None, poll_interval=0.005), timeout=4)
            finally:
                controller.cancel()
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(journey())
    assert provider.cancelled == 1
    assert interview.future is None
    assert interview.iv["status"] == "active"
    assert not any(message["text"] == "아직 입력 중인 문장" for message in interview.iv["messages"])


def test_default_terminal_uses_installed_prompt_and_patched_stdout(monkeypatch):
    import prompt_toolkit
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    monkeypatch.setenv("TERM", "xterm-256color")
    captured = []
    class RecordingOutput(DummyOutput):
        def write(self, text):
            captured.append(text)
        def write_raw(self, text):
            captured.append(text)
    actual_session = prompt_toolkit.PromptSession
    created = []
    def make_prompt(*args, **kwargs):
        prompt = actual_session(*args, **kwargs)
        created.append(prompt)
        return prompt
    monkeypatch.setattr(prompt_toolkit, "PromptSession", make_prompt)
    runner = Runner()
    runner.step = lambda: "feedback" if runner.calls else "active"
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=RecordingOutput()):
        async def drive():
            await _until(lambda: created and created[0].app.is_running)
            prompt = created[0]
            pipe.send_text("한글 질문")
            await _until(lambda: prompt.default_buffer.text == "한글 질문")
            runner.iv["messages"].append({"id": "stdout-1", "speaker": "candidate", "text": "출력 중에도 입력 보존"})
            # Let one real redraw/timer cycle run with patched stdout active.
            await asyncio.sleep(0.05)
            assert prompt.default_buffer.text == "한글 질문"
            pipe.send_text("\r")
        async def journey():
            controller = asyncio.create_task(drive())
            try:
                await asyncio.wait_for(terminal.run_terminal(runner, poll_interval=0.005), timeout=4)
                await controller
            finally:
                controller.cancel()
                if controller.done() and not controller.cancelled():
                    await controller
        asyncio.run(journey())
    assert runner.calls == [("speech", "한글 질문")]
    assert runner.closed
    assert "지원자(테스트): 출력 중에도 입력 보존" in "".join(captured)
