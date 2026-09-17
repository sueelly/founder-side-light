import json
from pathlib import Path
import subprocess

import httpx
import pytest

from bootstrap import check_platform
from harness.auth import ensure_claude_login
from harness.operator import check_server, submission_credentials
from harness.onboarding import bind_identity


def test_claude_login_is_completed_and_rechecked_without_saving_credentials(monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "API_SECRET")
    monkeypatch.setenv("FOUNDER_OPERATOR_TOKEN", "OPERATOR_SECRET")
    monkeypatch.setenv("FOUNDER_OPERATOR_EMAIL", "person@example.test")
    monkeypatch.setenv("FOUNDER_OPERATOR_CODE", "123456")

    def run(command, **kwargs):
        calls.append(command)
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        assert "FOUNDER_OPERATOR_TOKEN" not in kwargs["env"]
        assert "FOUNDER_OPERATOR_EMAIL" not in kwargs["env"]
        assert "FOUNDER_OPERATOR_CODE" not in kwargs["env"]
        assert Path(kwargs["cwd"]).is_dir()
        if command[-1] == "login":
            return subprocess.CompletedProcess(command, 0)
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "loggedIn": len(calls) == 3, "authMethod": "claude.ai", "email": "PRIVATE_EMAIL"}))

    ensure_claude_login(interactive=True, executable="claude", runner=run)
    assert [call[-1] for call in calls] == ["status", "login", "status"]
    assert "PRIVATE_EMAIL" not in capsys.readouterr().out


@pytest.mark.parametrize("payload", [
    {"loggedIn": False}, {"loggedIn": True, "authMethod": "api_key"},
    {"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "bedrock"},
    {"loggedIn": True}, [],
])
def test_invalid_or_api_key_only_auth_cannot_start(payload):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload))
    with pytest.raises(ValueError, match="로그인"):
        ensure_claude_login(executable="claude", runner=run)


def test_oauth_token_first_party_auth_is_accepted():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"}))

    assert ensure_claude_login(executable="claude", runner=run) == "claude"


def test_failed_login_does_not_create_or_allocate_session(tmp_path, monkeypatch):
    from harness import __main__ as cli
    def missing_login():
        raise ValueError("로그인이 필요합니다")
    monkeypatch.setattr(cli, "ensure_claude_login", missing_login)
    monkeypatch.setattr(cli, "allocate", lambda *args: pytest.fail("인증 전 배정"))
    destination = tmp_path / "session"
    assert cli.main(["--session", str(destination), "init", "--config", "missing.json", "--model", "sonnet"]) == 1
    assert not destination.exists()


def test_run_opens_feedback_document_after_interview(session, monkeypatch):
    from harness import __main__ as cli
    from harness import onboarding

    opened = []

    class FinishedRunner:
        last_error = None

        def __init__(self, *args, **kwargs):
            pass

        def step(self):
            return "feedback"

        def shutdown(self):
            pass

    monkeypatch.setattr(cli, "ensure_claude_login", lambda: None)
    monkeypatch.setattr(cli, "KakaoChannel", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "Interview", FinishedRunner)
    monkeypatch.setattr(onboarding, "open_document", lambda path: opened.append(path))

    cli.run_interview(session)

    assert opened == [session.interview_dir(session.current()) / "feedback.md"]


def test_start_checks_claude_before_any_server_login_or_kakao(tmp_path, monkeypatch):
    from harness import onboarding
    def missing_login(**kwargs):
        raise ValueError("Claude 로그인 필요")
    monkeypatch.setattr(onboarding, "ensure_claude_login", missing_login)
    monkeypatch.setattr(onboarding, "check_server", lambda *args: pytest.fail("인증 전 서버 호출"))
    with pytest.raises(ValueError, match="Claude"):
        onboarding.start(tmp_path / "session")


def test_submission_credentials_are_process_only_and_validate_code(monkeypatch):
    monkeypatch.delenv("FOUNDER_OPERATOR_EMAIL", raising=False)
    monkeypatch.delenv("FOUNDER_OPERATOR_CODE", raising=False)
    result = submission_credentials(
        "workshop", ask=lambda prompt: "person@example.test", secret=lambda prompt: "123456")
    assert result == {"workshop": "workshop", "email": "person@example.test", "code": "123456"}
    assert submission_credentials("workshop")["email"] == "person@example.test"
    monkeypatch.delenv("FOUNDER_OPERATOR_EMAIL", raising=False)
    monkeypatch.delenv("FOUNDER_OPERATOR_CODE", raising=False)
    emails = iter(["person@example.test", "person@example.test"])
    codes = iter(["bad", "123456"])
    assert submission_credentials("workshop", ask=lambda prompt: next(emails),
                                   secret=lambda prompt: next(codes))["code"] == "123456"


def test_resume_cannot_submit_as_another_participant(session):
    bind_identity(session, {"email": "person-1@example.test", "workshop": "workshop"})
    bind_identity(session, {"email": "person-1@example.test", "workshop": "workshop"})
    with pytest.raises(ValueError, match="다른 참가자"):
        bind_identity(session, {"email": "person-2@example.test", "workshop": "workshop"})


def test_windows_choice_cannot_install_mac_dependencies():
    check_platform("windows", "Windows")
    check_platform("mac", "Darwin")
    with pytest.raises(ValueError, match="운영체제"):
        check_platform("mac", "Windows")
    with pytest.raises(ValueError, match="운영체제"):
        check_platform("windows", "Darwin")


def test_submission_code_is_not_written_to_session(session, monkeypatch):
    from harness import onboarding
    import os
    config = session.config()
    config.operator.url = config.operator.base_url + "/api/lite"
    (session.directory / "config.json").write_text(config.model_dump_json())
    session.state["phase"] = "complete"
    session.save()
    monkeypatch.delenv("TEST_OPERATOR_EMAIL", raising=False)
    monkeypatch.delenv("TEST_OPERATOR_CODE", raising=False)
    monkeypatch.setattr(onboarding, "ensure_claude_login", lambda **kwargs: "claude")
    monkeypatch.setattr(onboarding, "check_server", lambda *args: {"status": "ok"})
    monkeypatch.setattr(onboarding, "submission_credentials", lambda *args, **kwargs: {
        "email": "person@example.test", "code": "123456", "workshop": "workshop"})
    assert onboarding.start(session.directory) == 0
    assert "123456" not in (session.directory / "state.json").read_text()
    assert "person@example.test" not in (session.directory / "state.json").read_text()


def test_old_bearer_config_is_migrated_to_email_code_fields(session):
    path = session.directory / "config.json"
    value = json.loads(path.read_text())
    value["operator"].pop("email_env", None)
    value["operator"].pop("code_env", None)
    value["operator"]["token_env"] = "FOUNDER_OPERATOR_TOKEN"
    path.write_text(json.dumps(value))
    config = session.config()
    assert config.operator.email_env == "FOUNDER_OPERATOR_EMAIL"
    assert config.operator.code_env == "FOUNDER_OPERATOR_CODE"
    migrated = json.loads(path.read_text())
    assert "token_env" not in migrated["operator"]


@pytest.mark.parametrize("selected", ["mac", "windows"])
def test_bootstrap_installs_missing_environment_and_reuses_it(tmp_path, monkeypatch, selected):
    import bootstrap
    from harness import auth
    (tmp_path / "requirements.lock.txt").write_text("dependency-lock")
    (tmp_path / "pyproject.toml").write_text("project")
    calls = []
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "check_platform", lambda choice: None)
    monkeypatch.setattr(bootstrap, "install_claude", lambda choice: calls.append("claude"))
    monkeypatch.setattr(auth, "ensure_claude_login", lambda **kwargs: calls.append("auth"))
    monkeypatch.setattr(bootstrap, "prepare_computer_use", lambda choice: calls.append("kakao-" + choice))
    python = tmp_path / ".venv" / ("Scripts/python.exe" if selected == "windows" else "bin/python")
    def run(command, **kwargs):
        calls.append([str(arg) for arg in command])
        if "venv" in [str(arg) for arg in command]:
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
    monkeypatch.setattr(bootstrap, "run", run)
    monkeypatch.chdir(tmp_path)
    args = ["--os", selected, "--uv", "uv"]
    bootstrap.main(args)
    assert "claude" in calls
    assert "auth" in calls
    assert any(isinstance(call, list) and "pip" in call and "install" in call for call in calls)
    assert any(isinstance(call, list) and call[-4:] == ["-m", "harness", "start", "--check-startup"] for call in calls)
    assert any(isinstance(call, list) and call[-1] == str(tmp_path) + "[test]" for call in calls)
    calls.clear()
    bootstrap.main(args)
    assert not any(isinstance(call, list) and "pip" in call and "install" in call for call in calls)
    # A stale stamp must not skip dependencies after the venv was removed.
    python.unlink()
    calls.clear()
    bootstrap.main(args)
    assert any(isinstance(call, list) and "pip" in call and "install" in call for call in calls)
