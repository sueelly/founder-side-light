"""Real, local fake CLI checks; these never contact Claude."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from harness.models import InterviewTurn
from harness.provider import Claude, ProviderError


@pytest.fixture
def context():
    return {"company": {"company_intro": "회사 공개 소개", "job_posting": "채용 공고", "culture_public": "공개 문화"}, "candidate": {"id": "P07", "name": "테스트", "resume": "공개 이력", "motivation": "공개 동기"},
            "insights": "사용자 기준", "messages": [], "phase": "core",
            "closing_stage": "interview", "remaining_seconds": 100}


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """A subprocess with the actual CLI's argv/stdin/result envelope."""
    source = tmp_path / "fake_claude.py"
    source.write_text("#!" + sys.executable + "\n" + '''import json, os, signal, subprocess, sys, time
from pathlib import Path
mode = os.environ.get("TEST_CLAUDE_MODE", "success")
if mode == "slow-input":
    time.sleep(.3)
data = sys.stdin.read()
child = None
if mode == "tree":
    child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(os.environ["TEST_CLAUDE_CAPTURE"]).write_text(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env_keys": sorted(os.environ), "input": data, "pid": os.getpid(), "child": child.pid if child else None}))
if mode in ("sleep", "tree"):
    time.sleep(60)
if mode == "failure":
    print("secret-auth-diagnostic", file=sys.stderr)
    sys.exit(17)
if mode == "malformed":
    print("not-json secret-auth-diagnostic")
elif mode == "invalid":
    print(json.dumps({"structured_output": {"text": ""}}))
else:
    print(json.dumps({"structured_output": {"text": "그때 어떤 역할을 맡으셨나요?"}}, ensure_ascii=False))
''', encoding="utf-8")
    source.chmod(0o755)
    executable = source
    if os.name == "nt":
        executable = tmp_path / "fake_claude.cmd"
        executable.write_text(f'@"{sys.executable}" "{source}" %*\r\n', encoding="utf-8")
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("TEST_CLAUDE_CAPTURE", str(capture))
    monkeypatch.setenv("TEST_CLAUDE_MODE", "success")
    return str(executable), capture


def wait_capture(path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(.01)
    pytest.fail("fake CLI did not start")


def assert_pid_exited(pid):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        # POSIX descendants can remain briefly as reparented zombies.
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
            return
        time.sleep(.02)
    pytest.fail(f"fake child still running: {pid}")


def test_cli_is_text_only_in_scratch_and_environment_is_sanitized(fake_cli, context, monkeypatch):
    secrets = {"FOUNDER_OPERATOR_TOKEN": "secret-token", "FOUNDER_OPERATOR_EMAIL": "secret-email",
               "FOUNDER_OPERATOR_CODE": "secret-code", "ANTHROPIC_API_KEY": "secret-api",
               "ANTHROPIC_AUTH_TOKEN": "secret-auth", "CLAUDE_CODE_OAUTH_TOKEN": "secret-oauth",
               "ANTHROPIC_BASE_URL": "https://invalid.example", "CLAUDE_CODE_USE_BEDROCK": "1",
               "CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_USE_FOUNDRY": "1", "CLAUDECODE": "1"}
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)
    executable, capture = fake_cli
    assert Claude("test", executable).generate("interviewer", context, InterviewTurn)["text"].startswith("그때")
    record = json.loads(capture.read_text())
    args = record["argv"]
    for flag, value in (("--tools", ""), ("--setting-sources", ""), ("--mcp-config", '{"mcpServers":{}}')):
        assert args[args.index(flag) + 1] == value
    assert json.loads(args[args.index("--settings") + 1])["disableAllHooks"] is True
    assert {"--strict-mcp-config", "--no-chrome", "--no-session-persistence", "--disable-slash-commands"} <= set(args)
    assert not set(secrets) & set(record["env_keys"])
    assert json.loads(record["input"]) == context
    assert not Path(record["cwd"]).exists()
    assert Path(record["cwd"]) != Path.cwd()


@pytest.mark.parametrize("mode", ["failure", "malformed", "invalid"])
def test_error_does_not_expose_output(fake_cli, context, monkeypatch, mode):
    monkeypatch.setenv("TEST_CLAUDE_MODE", mode)
    with pytest.raises(ProviderError) as caught:
        Claude("test", fake_cli[0]).generate("interviewer", context, InterviewTurn)
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_role_boundary_is_checked_before_process_launch(fake_cli, context):
    context["persona"] = {"secret": "candidate-only"}
    with pytest.raises(ProviderError, match="입력"):
        Claude("test", fake_cli[0]).generate("interviewer", context, InterviewTurn)
    assert not fake_cli[1].exists()


def test_timeout_terminates_real_subprocess(fake_cli, context, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_MODE", "sleep")
    started = time.monotonic()
    with pytest.raises(ProviderError, match="시간"):
        Claude("test", fake_cli[0]).generate("interviewer", context, InterviewTurn, timeout=.3)
    assert time.monotonic() - started < 3
    if os.name != "nt":
        assert_pid_exited(wait_capture(fake_cli[1])["pid"])


def test_cancel_stops_process_and_allows_next_call(fake_cli, context, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_MODE", "sleep")
    provider = Claude("test", fake_cli[0])
    event = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as worker:
        job = worker.submit(provider.generate, "interviewer", context, InterviewTurn, 20, event)
        record = wait_capture(fake_cli[1])
        event.set()
        provider.cancel()
        with pytest.raises(ProviderError, match="취소"):
            job.result(timeout=3)
    if os.name != "nt":
        assert_pid_exited(record["pid"])
    monkeypatch.setenv("TEST_CLAUDE_MODE", "success")
    assert provider.generate("interviewer", context, InterviewTurn)["text"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group check; Windows taskkill is checked separately")
def test_cancel_kills_stubborn_process_tree(fake_cli, context, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_MODE", "tree")
    provider = Claude("test", fake_cli[0])
    with ThreadPoolExecutor(max_workers=1) as worker:
        job = worker.submit(provider.generate, "interviewer", context, InterviewTurn, 20)
        record = wait_capture(fake_cli[1])
        provider.cancel()
        with pytest.raises(ProviderError, match="취소"):
            job.result(timeout=3)
    assert_pid_exited(record["pid"])
    assert_pid_exited(record["child"])


def test_cancel_before_start_never_spawns(fake_cli, context):
    event = threading.Event()
    event.set()
    with pytest.raises(ProviderError, match="취소"):
        Claude("test", fake_cli[0]).generate("interviewer", context, InterviewTurn, cancel_event=event)
    assert not fake_cli[1].exists()


def test_cancel_during_spawn_is_not_lost(fake_cli, context, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_MODE", "sleep")
    provider = Claude("test", fake_cli[0])
    created, release = threading.Event(), threading.Event()
    real_popen = subprocess.Popen
    pids = []

    def delayed_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        pids.append(process.pid)
        created.set()
        assert release.wait(timeout=3)
        return process

    monkeypatch.setattr("harness.provider.subprocess.Popen", delayed_popen)
    with ThreadPoolExecutor(max_workers=1) as worker:
        job = worker.submit(provider.generate, "interviewer", context, InterviewTurn, 20)
        assert created.wait(timeout=3)
        provider.cancel()
        release.set()
        with pytest.raises(ProviderError, match="취소"):
            job.result(timeout=3)
    monkeypatch.setattr("harness.provider.subprocess.Popen", real_popen)
    if os.name != "nt":
        assert_pid_exited(pids[0])


def test_cancel_after_output_rejects_late_result(fake_cli, context, monkeypatch):
    provider = Claude("test", fake_cli[0])
    event = threading.Event()
    entered, release = threading.Event(), threading.Event()

    class DelayedOutput(InterviewTurn):
        @classmethod
        def model_validate(cls, value, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return super().model_validate(value, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as worker:
        job = worker.submit(provider.generate, "interviewer", context, DelayedOutput, 20, event)
        assert entered.wait(timeout=3)
        event.set()
        provider.cancel()
        release.set()
        with pytest.raises(ProviderError, match="취소"):
            job.result(timeout=3)


def test_event_alone_stops_worker_and_rejects_concurrent_call(fake_cli, context, monkeypatch):
    monkeypatch.setenv("TEST_CLAUDE_MODE", "sleep")
    provider = Claude("test", fake_cli[0])
    event = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as worker:
        job = worker.submit(provider.generate, "interviewer", context, InterviewTurn, 20, event)
        record = wait_capture(fake_cli[1])
        with pytest.raises(ProviderError, match="이미"):
            provider.generate("interviewer", context, InterviewTurn)
        event.set()
        with pytest.raises(ProviderError, match="취소"):
            job.result(timeout=3)
    if os.name != "nt":
        assert_pid_exited(record["pid"])


def test_windows_cancel_requests_process_tree_and_reaps(monkeypatch):
    """Command-path unit check only; does not claim a Windows live run."""
    from types import SimpleNamespace
    import harness.provider as module

    commands, waits = [], []
    process = SimpleNamespace(pid=321, poll=lambda: 0,
        wait=lambda **kwargs: waits.append(kwargs), kill=lambda: pytest.fail("already exited"))
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: commands.append(command))
    call = module._Call(process=process)
    module._terminate_tree(call)
    module._terminate_tree(call)
    assert commands == [["taskkill", "/PID", "321", "/T", "/F"]]
    assert waits == [{"timeout": 3}]


def test_large_input_is_fully_written_when_cli_reads_after_first_poll(fake_cli, context, monkeypatch):
    """Claude startup can exceed the poll interval before it drains stdin."""
    monkeypatch.setenv("TEST_CLAUDE_MODE", "slow-input")
    context["candidate"]["resume"] = "합성 공개 이력 " * 20000
    result = Claude("test", fake_cli[0]).generate("interviewer", context, InterviewTurn, timeout=3)
    assert result["text"].startswith("그때")
    record = json.loads(fake_cli[1].read_text())
    assert json.loads(record["input"]) == context
