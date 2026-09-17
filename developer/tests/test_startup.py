"""Terminal startup/authentication; no real model or personal session."""
import json
from pathlib import Path
import subprocess

import pytest

from harness.auth import ensure_claude_login


def test_claude_login_is_completed_and_rechecked_without_saving_credentials(monkeypatch, capsys):
    calls = []
    for key in ("ANTHROPIC_API_KEY", "FOUNDER_OPERATOR_TOKEN", "FOUNDER_OPERATOR_EMAIL", "FOUNDER_OPERATOR_CODE"):
        monkeypatch.setenv(key, "PRIVATE_SECRET")

    def run(command, **kwargs):
        calls.append(command)
        assert not any("PRIVATE_SECRET" == value for value in kwargs["env"].values())
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
    with pytest.raises(ValueError, match="로그인"):
        ensure_claude_login(executable="claude", runner=lambda command, **kwargs:
                            subprocess.CompletedProcess(command, 0, json.dumps(payload)))


def test_oauth_token_first_party_auth_is_accepted():
    assert ensure_claude_login(executable="claude", runner=lambda command, **kwargs:
                              subprocess.CompletedProcess(command, 0, json.dumps({
                                  "loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"}))) == "claude"


def test_failed_auth_cannot_create_session(tmp_path, monkeypatch):
    from harness import __main__ as cli
    monkeypatch.setattr(cli, "ensure_claude_login", lambda: (_ for _ in ()).throw(ValueError("로그인이 필요합니다")))
    destination = tmp_path / "session"
    assert cli.main(["--session", str(destination), "init"]) == 1
    assert not destination.exists()


def test_init_defaults_without_config_and_preserves_assignment(tmp_path, monkeypatch):
    from harness import __main__ as cli
    from harness.storage import Session
    monkeypatch.setattr(cli, "ensure_claude_login", lambda: None)
    monkeypatch.setattr("harness.onboarding.open_document", lambda path: None)
    directory = tmp_path / "session"
    arguments = ["--session", str(directory), "init"]
    assert cli.main(arguments) == 0
    first = Session(directory).state
    assert first["model"] == "sonnet"
    assert first["interviews"][0]["candidate_id"] == "P07"
    assert cli.main(arguments) == 0
    assert Session(directory).state == first
    for name in ("config.json", "operator-handoff.json", "final-payload.json", "receipt.json", "computer-use"):
        assert not (directory / name).exists()


def test_noninteractive_start_and_run_reject_without_consuming_input(tmp_path, monkeypatch):
    from harness import __main__ as cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["--session", str(tmp_path), "start"]) == 1
    assert cli.main(["--session", str(tmp_path), "run"]) == 1


@pytest.mark.parametrize("command", ["server-check", "computer-next", "schema", "resolve-sent"])
def test_removed_commands_are_not_available(command):
    from harness import __main__ as cli
    with pytest.raises(SystemExit) as error:
        cli.main([command])
    assert error.value.code == 2


def test_restore_requires_explicit_confirmation(tmp_path):
    from harness import __main__ as cli
    with pytest.raises(SystemExit) as error:
        cli.main(["--session", str(tmp_path), "restore-records"])
    assert error.value.code == 2


def test_confirmation_uses_preview_hash_and_reconfirms_changed_file(session):
    from harness.__main__ import confirm_start
    from harness.storage import sha
    path = session.directory / "insights.md"
    attempts = []
    def ask(prompt):
        attempts.append(prompt)
        if len(attempts) == 1:
            path.write_text(path.read_text() + "\n추가 기준\n", encoding="utf-8")
        return "시작"
    assert confirm_start(session, ask=ask)
    assert len(attempts) == 2
    assert session.current()["insights_confirmation"]["sha256"] == sha(session.insights())


def test_completed_finalize_validates_locally_without_claude(completed, monkeypatch):
    from harness import __main__ as cli
    from harness.finalize import prepare_final
    from test_terminal_contract import Provider
    prepare_final(completed, Provider())
    monkeypatch.setattr(cli, "ensure_claude_login", lambda: pytest.fail("완료된 순위에 인증 호출"))
    monkeypatch.setattr(cli, "Claude", lambda *args: pytest.fail("완료된 순위에 모델 생성"))
    cli.finalize(completed)


def test_run_waits_for_confirmation_and_opens_feedback_after_completion(session, monkeypatch):
    from harness import __main__ as cli
    from harness import interview, terminal
    opened = []
    events = []
    monkeypatch.setattr(cli, "ensure_claude_login", lambda: events.append("auth"))
    monkeypatch.setattr(cli, "Claude", lambda *args: object())
    monkeypatch.setattr("harness.onboarding.open_document", lambda path: opened.append(path))
    class FinishedRunner:
        def __init__(self, s, provider):
            events.append("runner")
            assert s.current()["insights_confirmation"]
            self.iv = s.current()
    async def fake_terminal(runner):
        events.append("terminal")
    monkeypatch.setattr(interview, "Interview", FinishedRunner)
    monkeypatch.setattr(terminal, "run_terminal", fake_terminal)
    def ask(prompt):
        events.append("confirm")
        return "시작"
    assert cli.run_interview(session, ask=ask)
    assert events == ["auth", "confirm", "runner", "terminal"]
    assert opened == [session.interview_dir(session.current()) / "feedback.md"]


def test_active_resume_does_not_reconfirm_insights(session, monkeypatch):
    from harness import __main__ as cli
    from harness import terminal
    from test_terminal_contract import runner, Clock
    clock = Clock()
    old = runner(session, clock)
    old.step()
    old.shutdown()
    monkeypatch.setattr(cli, "ensure_claude_login", lambda: None)
    monkeypatch.setattr(cli, "Claude", lambda *args: object())
    monkeypatch.setattr("harness.onboarding.open_document", lambda path: None)
    async def fake_terminal(runner):
        runner.shutdown()
    monkeypatch.setattr(terminal, "run_terminal", fake_terminal)
    assert cli.run_interview(session, ask=lambda prompt: pytest.fail("진행 중 면접 재확인"))


def test_startup_test_flag_is_removed():
    from harness import __main__ as cli
    with pytest.raises(SystemExit) as error:
        cli.main(["start", "--check-startup"])
    assert error.value.code == 2


def test_completed_cli_start_can_show_results_without_tty_or_auth(completed, monkeypatch):
    from harness import __main__ as cli
    from harness.finalize import prepare_final
    from test_terminal_contract import Provider
    prepare_final(completed, Provider())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli, "ensure_claude_login", lambda: pytest.fail("완료된 결과에 인증 호출"))
    monkeypatch.setattr("harness.onboarding.ensure_claude_login", lambda **kw: pytest.fail("완료된 결과에 인증 호출"))
    assert cli.main(["--session", str(completed.directory), "start"]) == 0


def test_noninteractive_new_start_never_allocates(tmp_path, monkeypatch):
    from harness import __main__ as cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("harness.onboarding.ensure_claude_login", lambda **kw: pytest.fail("터미널 없이 인증"))
    directory = tmp_path / "session"
    assert cli.main(["--session", str(directory), "start"]) == 1
    assert not directory.exists()
