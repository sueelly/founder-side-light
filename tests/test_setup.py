import subprocess
import threading

import pytest

from harness import onboarding
from harness.checks import StartupChecks
from harness.storage import Session


@pytest.mark.parametrize("actual, expected", [("Darwin", "mac"), ("Windows", "windows")])
def test_bootstrap_detects_platform_without_a_prompt(actual, expected, monkeypatch):
    import bootstrap
    monkeypatch.setattr(bootstrap.platform, "system", lambda: actual)
    assert bootstrap.detect_platform() == expected


def test_unsupported_platform_is_explained(monkeypatch):
    import bootstrap
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Unknown")
    with pytest.raises(ValueError, match="Mac|Windows"):
        bootstrap.detect_platform()


@pytest.mark.parametrize("selected", ["mac", "windows"])
def test_bootstrap_installs_environment_once_and_runs_terminal_start(tmp_path, monkeypatch, selected):
    import bootstrap
    from harness import auth
    (tmp_path / "requirements.lock.txt").write_text("dependency-lock")
    (tmp_path / "pyproject.toml").write_text("project")
    calls = []
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "detect_platform", lambda: selected)
    monkeypatch.setattr(bootstrap, "resolve_uv", lambda *args: "uv")
    monkeypatch.setattr(bootstrap, "install_claude", lambda choice: calls.append("claude"))
    monkeypatch.setattr(auth, "ensure_claude_login", lambda **kwargs: calls.append("auth"))
    python = tmp_path / ".venv" / ("Scripts/python.exe" if selected == "windows" else "bin/python")

    def run(command, **kwargs):
        calls.append([str(arg) for arg in command])
        if "venv" in [str(arg) for arg in command]:
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
    monkeypatch.setattr(bootstrap, "run", run)
    monkeypatch.chdir(tmp_path)
    bootstrap.main([])
    assert "claude" in calls and "auth" in calls
    assert any(isinstance(call, list) and "pip" in call and "install" in call for call in calls)
    assert any(isinstance(call, list) and call[-4:] == ["-m", "harness", "start", "--check-startup"] for call in calls)
    calls.clear()
    bootstrap.main([])
    assert not any(isinstance(call, list) and "pip" in call and "install" in call for call in calls)
    python.unlink()
    calls.clear()
    bootstrap.main([])
    assert any(isinstance(call, list) and "pip" in call and "install" in call for call in calls)


def test_install_failure_never_starts_or_stamps_session(tmp_path, monkeypatch):
    import bootstrap
    from harness import auth
    for name in ("requirements.lock.txt", "pyproject.toml"):
        (tmp_path / name).write_text("fixture")
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "detect_platform", lambda: "windows")
    monkeypatch.setattr(bootstrap, "install_claude", lambda os: None)
    monkeypatch.setattr(auth, "ensure_claude_login", lambda **kwargs: None)
    monkeypatch.setattr(bootstrap, "install_environment", lambda *args: (_ for _ in ()).throw(OSError("install failed")))
    monkeypatch.setattr(bootstrap, "run", lambda *a, **kw: pytest.fail("설치 실패 후 면접 시작"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="install.log"):
        bootstrap.main([])
    assert not (tmp_path / ".bootstrap" / "installed.json").exists()
    assert not (tmp_path / ".runtime").exists()


@pytest.mark.parametrize("result", [0, 1, 5, "timeout", "missing"])
def test_background_checks_fail_closed_and_do_not_inherit_old_credentials(tmp_path, monkeypatch, result):
    monkeypatch.setenv("FOUNDER_OPERATOR_CODE", "TEST_SECRET")
    entered, release = threading.Event(), threading.Event()
    commands = []

    def run(command, **kwargs):
        assert "FOUNDER_OPERATOR_CODE" not in kwargs["env"]
        commands.append(command)
        entered.set()
        assert release.wait(5)
        if result == "timeout":
            raise subprocess.TimeoutExpired(command, 180)
        if result == "missing":
            raise OSError("missing executable")
        return subprocess.CompletedProcess(command, result)

    checks = StartupChecks(tmp_path, runner=run)
    assert entered.wait(5)
    release.set()
    if result == 0:
        checks.wait()
        assert [c[-2:] for c in commands] == [["pytest", "-q"], ["harness", "doctor"]]
        assert "로컬 점검 통과" in checks.log_path.read_text()
    else:
        with pytest.raises(ValueError, match="로컬 점검을 통과하지 못했습니다"):
            checks.wait()
    assert "TEST_SECRET" not in checks.log_path.read_text()


def test_check_failure_stops_before_new_session(tmp_path, monkeypatch):
    from harness import checks
    class FailingChecks:
        def wait(self):
            raise ValueError("로컬 점검 실패")
    monkeypatch.setattr(checks, "StartupChecks", FailingChecks)
    monkeypatch.setattr(onboarding, "ensure_claude_login", lambda **kw: None)
    with pytest.raises(ValueError, match="로컬 점검 실패"):
        onboarding.start(tmp_path / "session", check_startup=True)
    assert not (tmp_path / "session").exists()


def test_start_only_requests_insights_confirmation_and_keeps_assignment(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "ensure_claude_login", lambda **kw: None)
    monkeypatch.setattr("harness.__main__.ensure_claude_login", lambda: None)
    monkeypatch.setattr(onboarding, "open_document", lambda path: None)
    directory = tmp_path / "session"
    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        return "종료"
    assert onboarding.start(directory, ask=ask) == 0
    first = Session(directory).state
    assert first["interviews"][0]["candidate_id"] == "P07"
    assert onboarding.start(directory, ask=ask) == 0
    assert Session(directory).state == first
    assert all("시작" in prompt for prompt in prompts)
    assert all("이메일" not in prompt and "운영체제" not in prompt and "카카오" not in prompt for prompt in prompts)
