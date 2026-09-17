"""Git checkout startup: explicit setup beforehand, real user decisions during start."""
import subprocess

import pytest

from harness import onboarding
from harness.storage import Session


def test_start_checks_auth_without_login_browser_or_setup_processes(tmp_path, monkeypatch):
    from harness import __main__ as cli
    calls = []
    def check_auth(**kwargs):
        assert not kwargs.get("interactive", False)
        calls.append("auth-status")
    monkeypatch.setattr(onboarding, "ensure_claude_login", check_auth)
    monkeypatch.setattr(cli, "ensure_claude_login", check_auth)
    monkeypatch.setattr(onboarding, "open_document", lambda path: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("시작 중 설치 또는 자동 검사 실행"))
    assert onboarding.start(tmp_path / "session", ask=lambda prompt: "종료") == 0
    assert calls and not (tmp_path / ".bootstrap").exists()


def test_failed_auth_stops_before_creating_a_session(tmp_path, monkeypatch):
    def missing_auth(**kwargs):
        assert not kwargs.get("interactive", False)
        raise ValueError("claude auth login으로 로그인하세요")
    monkeypatch.setattr(onboarding, "ensure_claude_login", missing_auth)
    with pytest.raises(ValueError, match="claude auth login"):
        onboarding.start(tmp_path / "session", ask=lambda prompt: pytest.fail("인증 실패 후 입력"))
    assert not (tmp_path / "session").exists()


def test_start_only_requests_insights_confirmation_and_keeps_assignment(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "ensure_claude_login", lambda: None)
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
    assert prompts and all("시작" in prompt for prompt in prompts)
    assert all("이메일" not in prompt and "운영체제" not in prompt for prompt in prompts)


def test_completed_start_is_local_without_authentication_or_input(completed, monkeypatch, capsys):
    from harness.finalize import prepare_final
    from test_terminal_contract import Provider
    prepare_final(completed, Provider())
    before = (completed.directory / "state.json").read_bytes()
    monkeypatch.setattr(onboarding, "ensure_claude_login", lambda **kw: pytest.fail("완료된 세션 인증"))
    monkeypatch.setattr(onboarding, "open_document", lambda path: pytest.fail("완료된 세션 입력 문서"))
    assert onboarding.start(completed.directory, ask=lambda prompt: pytest.fail("완료된 세션 입력")) == 0
    assert (completed.directory / "state.json").read_bytes() == before
    assert str(completed.directory / "ranking.md") in capsys.readouterr().out


def test_feedback_resume_waits_for_user_instead_of_confirming_automatically(session, monkeypatch):
    from test_terminal_contract import finish, Clock
    finish(session, Clock())
    monkeypatch.setattr(onboarding, "ensure_claude_login", lambda: None)
    monkeypatch.setattr(onboarding, "open_document", lambda path: None)
    before = (session.directory / "state.json").read_bytes()
    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        return "종료"
    assert onboarding.start(session.directory, ask=ask) == 0
    assert prompts and "평가" in prompts[0]
    assert (session.directory / "state.json").read_bytes() == before
