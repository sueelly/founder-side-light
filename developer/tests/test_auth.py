"""Authentication checks use synthetic CLI responses, never participant credentials."""
import json
import subprocess

import pytest

from harness.auth import claude_environment, ensure_claude_login


@pytest.mark.parametrize("method,provider", [("claude.ai", "firstParty"), ("oauth_token", "firstParty"), ("oauth_token", "")])
def test_accepts_only_participant_oauth(method, provider):
    def run(command, **kwargs):
        assert command == ["synthetic-claude", "auth", "status"]
        return subprocess.CompletedProcess(command, 0, json.dumps({"loggedIn": True, "authMethod": method, "apiProvider": provider}))
    assert ensure_claude_login(executable="synthetic-claude", runner=run) == "synthetic-claude"


@pytest.mark.parametrize("status", [{"loggedIn": False}, {"loggedIn": True, "authMethod": "api_key"},
    {"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "bedrock"},
    {"loggedIn": True, "authMethod": "unknown"}, {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "vertex"}])
def test_rejects_other_auth_without_interactive_login(status):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(status), "secret diagnostic")
    with pytest.raises(ValueError, match="로그인") as caught:
        ensure_claude_login(executable="synthetic-claude", runner=run)
    assert len(calls) == 1
    assert "secret" not in str(caught.value)


def test_environment_drops_nested_code_and_legacy_operator_auth(monkeypatch):
    excluded = ["CLAUDECODE", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL", "FOUNDER_OPERATOR_TOKEN", "FOUNDER_OPERATOR_EMAIL", "FOUNDER_OPERATOR_CODE",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"]
    for key in excluded:
        monkeypatch.setenv(key, "synthetic-secret")
    monkeypatch.setenv("PATH", "/synthetic/path")
    env = claude_environment()
    assert not set(excluded) & set(env)
    assert env["PATH"] == "/synthetic/path"


def test_interactive_login_once_then_recheck_and_no_desktop_dependency(capsys):
    calls = []
    def run(command, **kwargs):
        calls.append(command[-1])
        if command[-1] == "login":
            return subprocess.CompletedProcess(command, 0)
        return subprocess.CompletedProcess(command, 0, json.dumps({"loggedIn": len(calls) == 3, "authMethod": "claude.ai"}))
    ensure_claude_login(interactive=True, executable="synthetic-claude", runner=run)
    assert calls == ["status", "login", "status"]
    assert "Desktop" not in capsys.readouterr().out


def test_missing_auth_directs_explicit_login_without_launching_browser():
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"loggedIn":false}')
    with pytest.raises(ValueError, match="claude auth login"):
        ensure_claude_login(executable="synthetic-claude", runner=run)
    assert calls == [["synthetic-claude", "auth", "status"]]


def test_missing_cli_directs_readme_installation(monkeypatch, tmp_path):
    from harness import auth
    monkeypatch.setattr(auth.shutil, "which", lambda name: None)
    monkeypatch.setattr(auth.Path, "home", lambda: tmp_path)
    with pytest.raises(ValueError, match="README") as caught:
        auth.claude_executable()
    assert "실행.command" not in str(caught.value)
    assert "실행.bat" not in str(caught.value)
