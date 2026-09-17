"""Local participant journey. All confirmations are supplied by the real user."""
from pathlib import Path
import subprocess
import sys

from .auth import ensure_claude_login
from .storage import Session


def open_document(path):
    path = str(Path(path).resolve())
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-t", path])
        elif sys.platform == "win32":
            subprocess.Popen(["notepad.exe", path])
    except OSError:
        pass  # The path remains usable if no external editor is registered.
    print(f"작성할 파일: {path}", flush=True)


def start(directory, ask=input, *, check_startup=False, model="sonnet"):
    from .__main__ import finalize, run_interview, session_lock
    from .checks import StartupChecks
    ensure_claude_login(interactive=True)
    directory = Path(directory).resolve()
    with session_lock(directory):
        if check_startup:
            StartupChecks().wait()
        existing = (directory / "state.json").exists()
        session = Session(directory) if existing else Session.create(directory, model=model)
        if session.state["phase"] != "complete":
            session.validate_runtime()
        while True:
            if session.state["phase"] == "complete":
                session.check_integrity()
                print(f"4회 면접과 로컬 순위 저장이 완료됐습니다: {directory / 'ranking.md'}")
                return 0
            if session.state["phase"] == "finalizing":
                finalize(session)
                return 0
            iv = session.current()
            if iv["status"] == "feedback":
                open_document(session.interview_dir(iv) / "feedback.md")
                print(f"{iv['name']} 면접의 본인 평가를 작성하세요. 인사이트: {directory / 'insights.md'}")
                while True:
                    answer = ask("평가 작성 후 인사이트 수정 여부 [수정 / 그대로], 나가려면 '종료': ").strip()
                    if answer in {"종료", "exit", "quit"}:
                        return 0
                    action = {"수정": "updated", "그대로": "unchanged", "updated": "updated", "unchanged": "unchanged"}.get(answer)
                    if action is None:
                        continue
                    try:
                        session.accept_feedback(action)
                        break
                    except ValueError as exc:
                        print(str(exc), flush=True)
                continue
            if iv["status"] == "ready":
                open_document(directory / "insights.md")
            # The run command owns the same confirmation flow; active resume
            # deliberately keeps the prior insight snapshot and deadline.
            if not run_interview(session, ask=ask):
                return 0
