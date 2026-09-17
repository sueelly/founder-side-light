"""One isolated, cancellable Claude text call; no tools or shared project state."""
from dataclasses import dataclass, field
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from .auth import claude_environment, claude_executable
from .candidate import validate_context
from .storage import resource


class ProviderError(RuntimeError):
    """A safe message suitable for a terminal; never raw CLI output."""


class ProviderCancelled(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


@dataclass
class _Call:
    cancelled: threading.Event = field(default_factory=threading.Event)
    process: object = None
    termination_lock: threading.Lock = field(default_factory=threading.Lock)
    terminated: bool = False


def _terminate_tree(call):
    """Terminate descendants as well as the CLI, and always reap the CLI."""
    with call.termination_lock:
        process = call.process
        if process is None or call.terminated:
            return
        call.terminated = True
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=5, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.kill()
        else:
            # start_new_session makes PID also the process-group ID. Kill the
            # group even if its leader already exited but a child holds a pipe.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=.25)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


class Claude:
    def __init__(self, model, executable="claude"):
        if executable == "claude":
            executable = claude_executable()
        if not model or not shutil.which(executable):
            raise ValueError("Claude CLI 설치·로그인과 모델 설정이 필요합니다")
        self.model, self.executable = model, executable
        self._lock = threading.Lock()
        self._active = None

    def cancel(self):
        """Cancel the current attempt, including cancellation during Popen."""
        with self._lock:
            call = self._active
            if call is None:
                return
            call.cancelled.set()
        # Do not hold the registration lock while waiting for process exit.
        # A process spawned concurrently will see this event after registration.
        _terminate_tree(call)

    @staticmethod
    def _check(call, cancel_event, deadline):
        if call.cancelled.is_set() or (cancel_event is not None and cancel_event.is_set()):
            raise ProviderCancelled("Claude 생성이 취소되었습니다") from None
        if time.monotonic() >= deadline:
            raise ProviderTimeout("Claude 생성 시간 초과") from None

    def generate(self, role, context, output_type, timeout=90, cancel_event=None):
        if timeout <= 0:
            raise ProviderTimeout("Claude 생성 시간 초과") from None
        try:
            data = validate_context(role, context)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError("Claude 역할별 입력 범위가 올바르지 않습니다") from None
        call = _Call()
        deadline = time.monotonic() + timeout
        with self._lock:
            if self._active is not None:
                raise ProviderError("Claude 생성이 이미 진행 중입니다") from None
            self._active = call
        try:
            self._check(call, cancel_event, deadline)
            command = [
                self.executable, "--print", "--output-format", "json",
                "--json-schema", json.dumps(output_type.model_json_schema(), ensure_ascii=False),
                "--model", self.model, "--system-prompt", resource("prompts", role + ".md"),
                "--no-session-persistence", "--permission-mode", "dontAsk", "--tools", "",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--disable-slash-commands", "--no-chrome", "--setting-sources", "",
                "--settings", '{"disableAllHooks":true}',
            ]
            with tempfile.TemporaryDirectory(prefix="founder-terminal-") as scratch:
                watcher_stop = threading.Event()
                watcher = None
                try:
                    self._check(call, cancel_event, deadline)
                    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=scratch,
                        env=claude_environment(), **options)
                    with self._lock:
                        call.process = process
                    self._check(call, cancel_event, deadline)

                    def watch_cancellation():
                        while not watcher_stop.wait(.05):
                            if call.cancelled.is_set() or (cancel_event is not None and cancel_event.is_set()):
                                _terminate_tree(call)
                                return

                    watcher = threading.Thread(target=watch_cancellation,
                                               name="claude-cancellation", daemon=True)
                    watcher.start()
                    # communicate must own stdin until the complete input and EOF
                    # have been sent. Retrying after a short timeout with input=None
                    # can strand unsent bytes when a slow-starting CLI fills the
                    # pipe. The watcher provides cancellation during this one call.
                    try:
                        stdout, _ = process.communicate(input=json.dumps(data, ensure_ascii=False),
                            timeout=max(.001, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        self._check(call, cancel_event, deadline)
                        raise ProviderTimeout("Claude 생성 시간 초과") from None
                    self._check(call, cancel_event, deadline)
                    if process.returncode:
                        raise ProviderError(f"Claude가 exit {process.returncode}로 종료했습니다") from None
                    try:
                        value = json.loads(stdout)
                        if not isinstance(value, dict) or value.get("is_error"):
                            raise ValueError("invalid envelope")
                        value = value.get("structured_output", value.get("result", value))
                        if isinstance(value, str):
                            value = json.loads(value)
                        result = output_type.model_validate(value).model_dump()
                    except (ValueError, TypeError, AttributeError):
                        raise ProviderError("Claude 응답 형식이 올바르지 않습니다") from None
                    self._check(call, cancel_event, deadline)
                    return result
                except (OSError, UnicodeError):
                    raise ProviderError("Claude 실행 또는 출력 읽기에 실패했습니다") from None
                finally:
                    watcher_stop.set()
                    # A caller can set only cancel_event; this worker still
                    # terminates and reaps the process before returning.
                    if call.process is not None:
                        if call.process.poll() is None or call.cancelled.is_set() or (cancel_event is not None and cancel_event.is_set()):
                            _terminate_tree(call)
                        try:
                            call.process.communicate(timeout=1)
                        except (OSError, ValueError, subprocess.TimeoutExpired):
                            _terminate_tree(call)
                        if watcher is not None:
                            watcher.join(timeout=1)
                        for stream in (call.process.stdin, call.process.stdout, call.process.stderr):
                            if stream is not None:
                                stream.close()
        finally:
            with self._lock:
                if self._active is call:
                    self._active = None
