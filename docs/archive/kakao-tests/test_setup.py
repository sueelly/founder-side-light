import subprocess
import threading
import zipfile

import pytest

from harness import onboarding
from harness.checks import StartupChecks
from harness.models import Config
from harness.storage import Session


def test_install_failure_keeps_wizard_and_session_from_starting(tmp_path, monkeypatch):
    import bootstrap
    from harness import auth
    for name in ("requirements.lock.txt", "pyproject.toml"):
        (tmp_path / name).write_text("fixture")
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "check_platform", lambda os: None)
    monkeypatch.setattr(bootstrap, "install_claude", lambda os: None)
    monkeypatch.setattr(auth, "ensure_claude_login", lambda **kwargs: None)
    def fail_install(*args):
        raise OSError("package download failed")
    monkeypatch.setattr(bootstrap, "install_environment", fail_install)
    monkeypatch.setattr(bootstrap, "prepare_computer_use", lambda os: pytest.fail("설치 실패 후 연결"))
    monkeypatch.setattr(bootstrap, "run", lambda *a, **kw: pytest.fail("설치 실패 후 면접 시작"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="install.log"):
        bootstrap.main(["--os", "windows"])
    assert not (tmp_path / ".bootstrap" / "installed.json").exists()
    assert not (tmp_path / ".runtime").exists()


@pytest.mark.parametrize("result", [0, 1, 5, "timeout", "missing"])
def test_background_checks_report_failure_and_never_inherit_submission_secrets(tmp_path, monkeypatch, result):
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
    assert entered.wait(5)  # Constructor returned while checks are still running.
    release.set()
    if result == 0:
        checks.wait()
        assert [c[-2:] for c in commands] == [["pytest", "-q"], ["harness", "doctor"]]
        assert "로컬 점검 통과" in checks.log_path.read_text()
    else:
        with pytest.raises(ValueError, match="로컬 점검을 통과하지 못했습니다"):
            checks.wait()
    assert "TEST_SECRET" not in checks.log_path.read_text()


@pytest.mark.parametrize("existing", [False, True])
def test_check_failure_stops_before_allocation_or_interview(session, tmp_path, monkeypatch, existing):
    from harness import checks
    directory = session.directory if existing else tmp_path / "new-session"
    before = (session.directory / "state.json").read_bytes()
    events = []

    class FailingChecks:
        def __init__(self):
            events.append("checks-start")

        def wait(self):
            events.append("checks-wait")
            raise ValueError("로컬 점검 실패")

    monkeypatch.setattr(checks, "StartupChecks", FailingChecks)
    monkeypatch.setattr(onboarding, "ensure_claude_login", lambda **kw: None)
    monkeypatch.setattr(onboarding, "check_server", lambda url: {"status": "ok"})
    monkeypatch.setattr(onboarding, "submission_credentials", lambda *a, **kw: {
        "email": "example@example.test", "code": "123456", "workshop": "workshop"})
    monkeypatch.setattr(onboarding, "configure_with_retry", lambda *a: (
        events.append("input"), Config(own_author="me"))[1])
    monkeypatch.setattr(onboarding, "allocate", lambda *a: pytest.fail("점검 실패 후 배정"))
    monkeypatch.setattr(onboarding, "child", lambda *a: pytest.fail("점검 실패 후 면접"))
    # Use an allowed destination for this test's saved configuration.
    config = session.config()
    config.operator.url = config.operator.base_url + "/api/lite"
    (session.directory / "config.json").write_text(config.model_dump_json())
    with pytest.raises(ValueError, match="로컬 점검 실패"):
        onboarding.start(directory, check_startup=True)
    assert events == (["checks-start", "checks-wait"] if existing
                      else ["checks-start", "input", "checks-wait"])
    assert (session.directory / "state.json").read_bytes() == before
    if not existing:
        assert not directory.exists()


def test_setup_creates_once_then_resumes_same_assignment(tmp_path, monkeypatch):
    from harness import checks
    events = []
    class PassingChecks:
        def __init__(self):
            events.append("checks-start")
        def wait(self):
            events.append("checks-pass")
    monkeypatch.setattr(checks, "StartupChecks", PassingChecks)
    monkeypatch.setattr(onboarding, "ensure_claude_login", lambda **kw: None)
    monkeypatch.setattr(onboarding, "check_server", lambda url: {"status": "ok", "capability": "founder-light-1"})
    monkeypatch.setattr(onboarding, "submission_credentials", lambda *a, **kw: {
        "email": "example@example.test", "code": "654321", "workshop": "workshop"})
    monkeypatch.setattr(onboarding, "configure_with_retry", lambda *a: Config(
        own_author="me", pairing_verified=True, shared_peer={"chat": "room", "author": "peer"}))
    monkeypatch.setattr(onboarding, "open_document", lambda path: None)
    monkeypatch.setattr(onboarding, "child", lambda *a: pytest.fail("사용자 시작 전 면접"))
    directory = tmp_path / "new-session"
    assert onboarding.start(directory, ask=lambda prompt: "종료", check_startup=True) == 0
    first = Session(directory).state
    assert first["interviews"][0]["candidate_id"] == "P07"
    monkeypatch.setattr(onboarding, "allocate", lambda *a: pytest.fail("재시작 시 재배정"))
    monkeypatch.setattr(onboarding, "configure_with_retry", lambda *a: pytest.fail("저장된 대화방 재입력"))
    assert onboarding.start(directory, ask=lambda prompt: "종료", check_startup=True) == 0
    assert Session(directory).state == first
    assert events == ["checks-start", "checks-pass"] * 2
    for name in ("config.json", "state.json", "operator-handoff.json"):
        assert "654321" not in (directory / name).read_text()
        assert "example@example.test" not in (directory / name).read_text()


def test_server_retry_keeps_wizard_alive(monkeypatch):
    results = iter([ValueError("offline"), {"status": "ok"}])
    def check(url):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(onboarding, "check_server", check)
    assert onboarding.wait_for_server("https://example.test", ask=lambda prompt: "") == {"status": "ok"}


def test_noninteractive_start_cannot_collect_credentials(monkeypatch):
    from harness import __main__ as cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(onboarding, "start", lambda *a, **kw: pytest.fail("도구 stdin으로 인증 입력"))
    assert cli.main(["start", "--check-startup"]) == 1


def test_distribution_contains_setup_and_checks_without_local_data(tmp_path, monkeypatch):
    from pathlib import Path
    from scripts import package
    import shutil
    source = package.ROOT
    for path in source.iterdir():
        if path.is_file() and path.name in {
            "실행.command", "실행.bat", "start-windows.ps1", "bootstrap.py", "setup.md",
            "pyproject.toml", "requirements.lock.txt", "README.md", "CLAUDE.md", "AGENTS.md",
        }:
            shutil.copy2(path, tmp_path / path.name)
    for folder in ("harness", "materials", "prompts", "templates", "docs", "tests", "scripts", "config"):
        shutil.copytree(source / folder, tmp_path / folder, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "config" / "local.json").write_text("PRIVATE_CONFIGURATION")
    for folder in (".runtime", ".bootstrap", ".venv"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "secret").write_text("PRIVATE_STATE")
    monkeypatch.setattr(package, "ROOT", tmp_path)
    with zipfile.ZipFile(package.build(windows=not (source / "실행.command").exists())) as archive:
        names = {Path(n).relative_to("founder-side-light").as_posix() for n in archive.namelist()}
        assert {"setup.md", "bootstrap.py", "tests/test_setup.py", "tests/conftest.py", "harness/checks.py"} <= names
        assert not any(n.startswith((".runtime/", ".bootstrap/", ".venv/")) for n in names)
        assert "config/local.json" not in names
        assert "harness/computer_use.py" in names
        assert "docs/computer-use.md" in names
        assert "harness/kakao_cli.py" not in names
        assert "harness/windows_kakao.py" not in names
        assert "pywin32" not in archive.read("founder-side-light/requirements.lock.txt").decode()
        assert archive.getinfo("founder-side-light/실행.bat").flag_bits & 0x800
