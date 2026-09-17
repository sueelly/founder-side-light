import json
import subprocess

import pytest

from harness.models import InterviewTurn
from harness.provider import Claude, ProviderError


def test_claude_is_text_only_and_never_gets_operator_credentials(monkeypatch):
    monkeypatch.setenv("FOUNDER_OPERATOR_TOKEN", "secret-only-for-http")
    monkeypatch.setenv("FOUNDER_OPERATOR_EMAIL", "person@example.test")
    monkeypatch.setenv("FOUNDER_OPERATOR_CODE", "123456")

    def run(command, **kwargs):
        assert command[command.index("--tools") + 1] == ""
        assert command[command.index("--setting-sources") + 1] == ""
        assert "--safe-mode" not in command
        assert "--strict-mcp-config" in command
        assert "--no-session-persistence" in command
        assert "FOUNDER_OPERATOR_TOKEN" not in kwargs["env"]
        assert "FOUNDER_OPERATOR_EMAIL" not in kwargs["env"]
        assert "FOUNDER_OPERATOR_CODE" not in kwargs["env"]
        assert "secret-only-for-http" not in kwargs["input"]
        assert "FOUNDER_OPERATOR_EMAIL" not in kwargs["input"]
        return subprocess.CompletedProcess(command, 0, json.dumps({"structured_output": {"text": "어떤 역할을 맡으셨나요?"}}), "")

    result = Claude("test", runner=run).generate("interviewer", {}, InterviewTurn)
    assert result["text"] == "어떤 역할을 맡으셨나요?"


def test_provider_errors_do_not_expose_stderr():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "secret diagnostic")

    with pytest.raises(ProviderError, match="exit 1") as error:
        Claude("test", runner=run).generate("interviewer", {}, InterviewTurn)
    assert "secret" not in str(error.value)


def test_invalid_structured_output_is_not_sent():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, '{"structured_output":{"text":""}}', "")

    with pytest.raises(ProviderError):
        Claude("test", runner=run).generate("interviewer", {}, InterviewTurn)
