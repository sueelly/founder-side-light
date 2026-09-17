"""Local terminal interview commands; the harness is the only session writer."""
import argparse
import asyncio
from pathlib import Path
import shutil
import sys
import tempfile
import time

from .allocation import choose_candidates, load_pools
from .auth import ensure_claude_login
from .provider import Claude
from .storage import Session, file_lock, resource, sha


def session_lock(directory):
    # One location for init, start and all state-changing commands, including
    # before the session directory exists. Never create a half-empty session.
    path = str(Path(directory).resolve())
    return file_lock(Path(tempfile.gettempdir()) / ("founder-terminal-" + sha(path)[:24] + ".lock"))


def doctor():
    from .candidate import validate_bundle
    for name in ("interviewer", "candidate", "ranker"):
        resource("prompts", name + ".md")
    for name in ("insights", "feedback"):
        resource("templates", name + ".md")
    available = validate_bundle()
    choose_candidates(load_pools(), available, seed=0)
    try:
        from prompt_toolkit import PromptSession  # noqa: F401 — installed runtime dependency
    except ImportError as exc:
        raise ValueError("터미널 입력 의존성이 없습니다. 실행 파일을 다시 열어 설치하세요.") from exc
    print("공개 후보 자료·확정 후보군 8명의 지원자 자료·프롬프트 정적 검사 통과")
    print("실행 4명 / 첫 면접관 사람 1800초 / 이후 Claude 면접관 1200초 / 지원자는 모두 Claude")
    print("인사이트 자동 추출 없음 / 매 후보 실제 사용자 평가 필수 / 최종 순위 로컬 저장")
    print("첫 배정은 P07 먼저, 확정 후보군 무작위 배정 후 세션 내 고정 (모델 호출 없음)")
    print(f"Claude CLI: {'설치됨' if shutil.which('claude') else '설치 필요'}")
    print("정적 검사 완료. 실제 Claude 인증·응답·Mac/Windows 터미널 입력은 별도 검증이 필요합니다.")


def print_status(session):
    print(f"세션 {session.state['session_id']} · {session.state['phase']}")
    for iv in session.state["interviews"]:
        print(f"{iv['number']}. {iv['name']} ({iv['candidate_id']}) · {iv['mode']} · {iv['status']}")
        if iv["status"] == "active":
            observed = max(time.time(), iv.get("last_seen_at", iv["started_at"]))
            print(f"   남은 시간: {max(0, int(iv['deadline_at'] - observed))}초 (재시작해도 연장되지 않음)")
    print(f"인사이트: {session.directory / 'insights.md'}")
    iv = session.current()
    if iv:
        print(f"기록: {session.interview_dir(iv) / 'transcript.md'}")
        if iv["status"] == "feedback":
            print(f"필수 사용자 평가: {session.interview_dir(iv) / 'feedback.md'}")


def confirm_start(session, ask=input):
    """Only real user confirmation can pin the exact displayed insights bytes."""
    while True:
        path = session.directory / "insights.md"
        # A template is shown too so users can edit it before confirming.
        markdown = path.read_text(encoding="utf-8")
        print(f"\n이번 면접 기준: {path}\n\n{markdown}", flush=True)
        expected_hash = sha(markdown)
        answer = ask("기준을 확인했으면 '시작', 파일을 수정했으면 Enter로 다시 확인, 나가려면 '종료': ").strip()
        if answer in {"종료", "exit", "quit"}:
            return False
        if answer != "시작":
            continue
        try:
            session.confirm_insights(expected_hash, at=time.time())
            return True
        except ValueError as exc:
            print(str(exc), flush=True)


def run_interview(session, ask=input):
    ensure_claude_login()
    session.check_integrity()
    session.validate_runtime()
    iv = session.current()
    if not iv or iv["status"] == "feedback":
        print_status(session)
        raise ValueError("현재 피드백을 확정하거나, 네 번째 피드백 이후 finalize를 실행하세요")
    if iv["status"] == "ready" and not confirm_start(session, ask):
        return False
    from .interview import Interview
    from .terminal import run_terminal
    runner = Interview(session, Claude(session.state["model"]))
    print(f"\n{iv['number']}/4 · {iv['name']} · {'사람 면접' if iv['mode'] == 'human' else 'Claude 면접'}", flush=True)
    print(f"기록: {session.interview_dir(iv) / 'transcript.md'}", flush=True)
    print("질문은 그대로 입력하세요. 명령: /last /end (사람 면접), /status /retry (모든 면접)", flush=True)
    print("Ctrl+C로 중단하면 모델 실행을 정리합니다. 같은 세션의 마감 시각은 유지됩니다.", flush=True)
    asyncio.run(run_terminal(runner))
    feedback_path = session.interview_dir(iv) / "feedback.md"
    print(f"면접 종료. 사용자 평가를 작성하세요: {feedback_path}")
    from .onboarding import open_document
    open_document(feedback_path)
    return True


def finalize(session):
    from .finalize import prepare_final
    session.check_integrity()
    provider = None
    if session.state["phase"] != "complete":
        ensure_claude_login()
        provider = Claude(session.state["model"])
    prepare_final(session, provider)
    print(f"최종 순위를 로컬에 저장했습니다: {session.directory / 'ranking.md'}", flush=True)


def init_session(args):
    ensure_claude_login()
    directory = Path(args.session)
    with session_lock(directory):
        if (directory / "state.json").exists():
            print("기존 세션의 배정을 유지합니다.")
            print_status(Session(directory))
            return
        session = Session.create(directory, model=args.model)
        print_status(session)
        path = directory / "insights.md"
        print(f"총 4명 세션을 만들었습니다. {path}에 기준을 먼저 작성하세요.")
        from .onboarding import open_document
        open_document(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="터미널·Claude 4명 인성 면접")
    parser.add_argument("--session", default=".runtime/terminal-light", help="세션 폴더 (하위 명령 앞에 지정)")
    commands = parser.add_subparsers(dest="command", required=True)
    start_parser = commands.add_parser("start", help="설치·로그인 확인 후 로컬 4명 면접 진행")
    start_parser.add_argument("--check-startup", action="store_true", help="로컬 테스트·doctor 실패 시 시작 차단")
    start_parser.add_argument("--model", default="sonnet")
    commands.add_parser("login", help="본인 Claude 로그인 확인·실행")
    init = commands.add_parser("init", help="기본 설정으로 새 세션 준비")
    init.add_argument("--model", default="sonnet")
    commands.add_parser("doctor", help="로컬 자료와 실행 의존성 정적 검사")
    commands.add_parser("run", help="기준 확인 후 면접 시작·재개")
    commands.add_parser("status")
    feedback = commands.add_parser("feedback", help="실제 사용자 평가와 기준 변경 여부 확정")
    feedback.add_argument("--insights", choices=["updated", "unchanged"], required=True)
    commands.add_parser("finalize", help="최종 순위를 로컬에 저장·검증")
    restore = commands.add_parser("restore-records", help="유효한 상태 원본에서 파생 기록 문서 복원")
    restore.add_argument("--confirmed-restore", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in {"start", "run"} and not sys.stdin.isatty():
            raise ValueError("실제 사용자 입력을 받는 터미널에서 실행하세요. Mac은 실행.command, Windows는 실행.bat를 여세요.")
        if args.command == "start":
            from .onboarding import start
            return start(args.session, check_startup=args.check_startup, model=args.model)
        if args.command == "login":
            ensure_claude_login(interactive=True)
            print("Claude 로그인 확인 완료")
            return 0
        if args.command == "doctor":
            doctor()
            return 0
        if args.command == "init":
            init_session(args)
            return 0
        if not (Path(args.session) / "state.json").exists():
            raise ValueError("먼저 init으로 세션을 만드세요")
        with session_lock(args.session):
            session = Session(args.session, repair=args.command == "restore-records")
            if args.command == "status":
                print_status(session)
            elif args.command == "run":
                run_interview(session)
            elif args.command == "feedback":
                iv = session.accept_feedback(args.insights)
                print(f"{iv['name']} 평가와 인사이트 변경 여부를 저장했습니다.", flush=True)
                if session.state["phase"] == "finalizing":
                    finalize(session)
                else:
                    print("다음 후보가 준비됐습니다. run으로 다음 면접을 시작하세요.")
            elif args.command == "finalize":
                finalize(session)
            elif args.command == "restore-records":
                print("상태 원본을 검증한 뒤 파생 기록 문서를 복원했습니다.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("중단했습니다. 같은 세션으로 재개하세요. 면접 마감 시각은 유지됩니다.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
