"""Participant-owned Claude login; never print or persist credentials."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def claude_executable():
    found = shutil.which("claude.exe" if sys.platform == "win32" else "claude")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    if candidate.is_file():
        return str(candidate)
    raise ValueError("Claude가 없습니다. Mac은 실행.command, Windows는 실행.bat를 여세요.")


def claude_environment():
    # Force the participant's Claude login rather than an inherited API/provider key.
    # Strip the legacy token too when a shell still has one from an older build.
    excluded = {"FOUNDER_OPERATOR_TOKEN", "FOUNDER_OPERATOR_EMAIL", "FOUNDER_OPERATOR_CODE",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDECODE"}
    return {key: value for key, value in os.environ.items()
            if key not in excluded and not key.startswith(("FOUNDER_OPERATOR_", "ANTHROPIC_"))}


def ensure_claude_login(*, interactive=False, executable=None, runner=None):
    runner = runner or subprocess.run
    executable = executable or claude_executable()
    env = claude_environment()
    with tempfile.TemporaryDirectory(prefix="founder-light-auth-") as scratch:
        def authenticated():
            try:
                result = runner([executable, "auth", "status"], capture_output=True, text=True,
                                encoding="utf-8", cwd=scratch, env=env, timeout=30)
                status = json.loads(result.stdout)
                if result.returncode != 0 or status.get("loggedIn") is not True:
                    return False
                method = str(status.get("authMethod", "")).lower()
                provider = str(status.get("apiProvider", "")).lower()
                # Claude Code versions report the participant's browser
                # OAuth session either as claude.ai or oauth_token/firstParty.
                # API-key and third-party provider sessions must never pass.
                if method in {"api_key", "api-key", "apikey"}:
                    return False
                if provider in {"bedrock", "vertex", "foundry"}:
                    return False
                return method == "claude.ai" or (
                    method == "oauth_token" and provider in {"", "firstparty", "first_party"}
                )
            except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
                return False

        if authenticated():
            return executable
        if interactive:
            print("면접용 Claude Code 인증이 필요합니다. 브라우저에서 본인 Claude 계정으로 로그인하세요.\n"
                  "API key는 필요 없습니다.", flush=True)
            result = runner([executable, "auth", "login"], cwd=scratch, env=env)
            if result.returncode == 0 and authenticated():
                return executable
    raise ValueError("Claude 로그인 확인에 실패했습니다. 실행 파일을 다시 열어 로그인하세요.")
