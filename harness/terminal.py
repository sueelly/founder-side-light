"""Async terminal rendering with one input buffer and no queued interview speech."""
import asyncio
from contextlib import nullcontext, suppress
import math


def status_line(runner):
    iv = runner.iv
    remaining = max(0, math.ceil(runner.remaining()))
    minutes, seconds = divmod(remaining, 60)
    mode = "사람 면접" if iv["mode"] == "human" else "Claude 면접"
    return (f"{iv['number']}/4 · {iv['name']} · {mode} · 남은 시간 {minutes:02d}:{seconds:02d}"
            f" · {runner.waiting_label()}")


def message_line(iv, message):
    speaker = f"지원자({iv['name']})" if message["speaker"] == "candidate" else "면접관"
    return f"{speaker}: {message['text']}"


def is_speech(text):
    return bool(text.strip()) and not text.strip().startswith("/")


def handle_input(runner, text, *, output=print):
    text = text.strip()
    if not text:
        return
    if text == "/status":
        output(status_line(runner))
        session = getattr(runner, "session", None)
        if session is not None:
            output(f"기록: {session.interview_dir(runner.iv) / 'transcript.md'}")
        return
    if text == "/retry":
        runner.retry_generation()
        return
    if text.startswith("/") and text not in {"/last", "/end"}:
        raise ValueError("사용할 수 있는 명령: /last /end /status /retry")
    if runner.iv["mode"] != "human":
        raise ValueError("Claude 면접에서는 /status와 /retry만 사용할 수 있습니다")
    if text == "/last":
        runner.request_last()
    elif text == "/end":
        runner.request_end()
    else:
        runner.human_question(text[2:].strip() if text.startswith("q ") else text)


def submit_input(runner, text, *, output=print):
    """Return rejected speech as an editable draft, never a future action."""
    try:
        handle_input(runner, text, output=output)
    except (ValueError, RuntimeError) as exc:
        output(f"입력이 접수되지 않았습니다: {exc}")
        if runner.iv["mode"] == "human" and is_speech(text):
            return text
    return ""


async def _read_input(prompt, label, draft, runner):
    # Keep Ctrl+C/EOF inside the owned task until the runner can clean up.
    try:
        return await prompt.prompt_async(
            label, default=draft, bottom_toolbar=lambda: status_line(runner),
            refresh_interval=0.2)
    except (KeyboardInterrupt, EOFError) as exc:
        return exc


async def run_terminal(runner, *, prompt=None, output=print, poll_interval=0.1):
    """Poll the nonblocking runner while prompt_toolkit owns Korean input/redraw."""
    interactive_prompt = prompt is None
    if interactive_prompt:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout
        prompt = PromptSession()
        stdout_context = patch_stdout()
    else:
        stdout_context = nullcontext()
    displayed = set()
    shown_error = None
    input_task = None
    draft = ""
    try:
        with stdout_context:
            while True:
                if input_task is not None and input_task.done():
                    # EOF/Ctrl+C propagate to the caller after cleaning up the model.
                    text = input_task.result()
                    input_task = None
                    if isinstance(text, BaseException):
                        raise text
                    draft = submit_input(runner, text, output=output)
                outcome = runner.step()
                for message in runner.iv["messages"]:
                    if message["id"] not in displayed:
                        output(message_line(runner.iv, message))
                        displayed.add(message["id"])
                if outcome == "feedback":
                    return
                if runner.last_error and runner.last_error != shown_error:
                    shown_error = runner.last_error
                    output(f"Claude 생성 오류: {shown_error}. /retry로 다시 요청할 수 있습니다. 타이머는 계속됩니다.")
                if not runner.last_error:
                    shown_error = None
                if input_task is None:
                    label = "면접관 > " if runner.iv["mode"] == "human" else "명령 > "
                    input_task = asyncio.create_task(_read_input(prompt, label, draft, runner))
                    draft = ""
                prompt.app.invalidate()
                await asyncio.sleep(poll_interval)
    finally:
        if input_task is not None:
            input_task.cancel()
            with suppress(asyncio.CancelledError, EOFError, KeyboardInterrupt):
                await input_task
        runner.shutdown()
