"""Opt-in, bounded Claude development check for interviewer and ranker.

All dialogue, criteria and feedback below are explicitly synthetic test data.
No Session is created, no user evaluation is accepted, and no ranking is saved
as an actual interview result. Existing verification artifacts are preserved.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import subprocess
import time

from harness.auth import ensure_claude_login
from harness.candidate import runtime_fingerprint, validate_context
from harness.finalize import validate_ranking
from harness.models import InterviewTurn, Ranking
from harness.provider import Claude, ProviderError
from harness.storage import atomic_write, canonical, resource, sha


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / ".runtime/verification/role-models"
SYNTHETIC = "합성 개발 검증 데이터이며 실제 지원자 발언이나 사용자 평가가 아닙니다."
INSIGHTS = "# 합성 검증 기준\n\n" + SYNTHETIC + "\n\n## 중요하게 보는 기준\n담당 범위의 책임감, 누락의 조기 공유, 동료와의 협업.\n"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def company():
    return {name: resource("materials", f"company/{name}.md")
            for name in ("company_intro", "job_posting", "culture_public")}


def interviewer_context():
    cid = "P07"
    name = json.loads(resource("materials", f"candidates/{cid}/application.json"))["name"]
    return {"company": company(), "candidate": {"id": cid, "name": name,
        "resume": resource("materials", f"candidates/{cid}/resume.txt"),
        "motivation": resource("materials", f"candidates/{cid}/support_motivation.md")},
        "insights": INSIGHTS, "phase": "core", "closing_stage": "interview", "remaining_seconds": 600.0,
        "messages": [
            {"id": "synthetic-message-1", "speaker": "interviewer", "at": 1.0,
             "text": "[합성 개발 검증 질문] 필요한 자료가 누락된 것을 발견했던 경험이 있나요?"},
            {"id": "synthetic-message-2", "speaker": "candidate", "at": 2.0,
             "text": "[합성 답변 · 실제 후보의 이력이 아님] 증빙 두 건이 빠져 있어서 담당자에게 확인하고, 팀에 먼저 누락 목록을 공유했습니다."}]}


def ranker_context():
    examples = [
        ("P07", "A", "누락을 발견한 당일 담당자와 팀에 알리고 완료 여부를 다시 확인했습니다.",
         "누락을 조기에 공유하고 후속 확인까지 한 점을 좋게 보았다."),
        ("P01", "B", "제 목록은 확인했지만 다른 담당자에게 알려 주는 것은 다음 날로 미뤘습니다.",
         "본인 담당 범위는 확인했지만 공유 시점은 더 확인할 필요가 있다."),
        ("P04", "C", "동료와 누락 목록을 함께 정리하고 담당자를 나눠 확인했습니다.",
         "협업과 역할 분담은 확인했으나 최종 완료 확인 여부는 불명확하다."),
        ("P06", "D", "자료가 빠진 줄 몰랐고 동료가 알려 준 뒤에야 확인했습니다.",
         "지적 뒤 확인은 했지만 사전 점검 방법은 확인하지 못했다."),
    ]
    candidates, sources = [], {}
    for number, (cid, name, answer, feedback) in enumerate(examples, 1):
        answer, feedback = "[합성 지원자 발언] " + answer, "[합성 사용자 평가 · 실제 판단 아님] " + feedback
        answer_id, feedback_id = f"{cid}:synthetic-answer", f"{cid}:synthetic-feedback"
        messages = [
            {"id": "synthetic-message-1", "source_id": f"{cid}:synthetic-question", "speaker": "interviewer",
             "text": "[합성 질문] 자료 누락을 발견했을 때 어떻게 행동했나요?", "at": 1.0},
            {"id": "synthetic-message-2", "source_id": answer_id, "speaker": "candidate", "text": answer, "at": 2.0},
        ]
        sources[answer_id] = {"candidate_id": cid, "text": answer}
        sources[feedback_id] = {"candidate_id": cid, "text": feedback}
        candidates.append({"candidate_id": cid, "name": "합성 지원자 " + name,
            "mode": "human" if number == 1 else "agent", "duration_seconds": 30.0,
            "insights_used": INSIGHTS, "messages": messages,
            "user_feedback": {"source_id": feedback_id, "text": feedback}, "protocol_note": SYNTHETIC})
    return {"final_insights": INSIGHTS, "company": company(), "candidates": candidates,
            "evidence_sources": sources, "validation_errors": []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", help="선택한 역할별 실제 본인 Claude 호출을 한 번씩 실행")
    parser.add_argument("--role", choices=("both", "interviewer", "ranker"), default="both")
    parser.add_argument("--run-id", default="", help="기존 결과를 보존할 새 검증 묶음 이름")
    args = parser.parse_args(argv)
    if not args.run_live:
        parser.error("실제 호출을 실행하려면 --run-live가 필요합니다")
    if args.run_id and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", args.run_id):
        raise ValueError("run-id는 영문 소문자·숫자·하이픈만 사용합니다")
    output_directory = OUTPUT / args.run_id if args.run_id else OUTPUT
    roles = ("interviewer", "ranker") if args.role == "both" else (args.role,)
    if any((output_directory / (name + ".json")).exists() for name in (*roles, "summary")):
        raise ValueError("기존 검증 결과가 있습니다. 자동 재호출하거나 덮어쓰지 않습니다")
    ensure_claude_login()
    provider = Claude("sonnet")
    version = subprocess.run([provider.executable, "--version"], capture_output=True, text=True,
                             timeout=10, check=True).stdout.strip()
    metadata = {"kind": "synthetic-development-fixtures-with-real-claude", "fixture_notice": SYNTHETIC,
        "model_requested": "sonnet", "model_resolved": "not exposed by provider", "cli_version": version,
        "platform": platform.system(), "python": platform.python_version(), "runtime_sha256": runtime_fingerprint(),
        "automatic_retry": False, "max_total_calls": len(roles), "max_parallel_calls": 1,
        "selected_roles": list(roles), "run_id": args.run_id}
    output_directory.mkdir(parents=True, exist_ok=True)
    results = []
    for role, context, output_type, timeout in (
        ("interviewer", interviewer_context(), InterviewTurn, 90),
        ("ranker", ranker_context(), Ranking, 120),
    ):
        if role not in roles:
            continue
        if runtime_fingerprint() != metadata["runtime_sha256"]:
            raise ValueError("검증 중 자료 또는 프롬프트가 변경됐습니다")
        validate_context(role, context)
        result = {**metadata, "role": role, "started_at": utc_now(),
                  "input_sha256": sha(canonical(context)), "prompt_sha256": sha(resource("prompts", role + ".md"))}
        started = time.monotonic()
        try:
            output = provider.generate(role, context, output_type, timeout=timeout)
            if role == "ranker":
                validate_ranking(output, [c["candidate_id"] for c in context["candidates"]], context["evidence_sources"])
                checks = ["schema", "four_unique_candidates", "unique_ranks_1_to_4", "actual_source_substrings", "same_candidate_evidence"]
            else:
                if "수고하셨습니다" in output["text"] or "면접 시작하겠습니다" in output["text"] or re.search(r"(?m)^(면접관|지원자)\s*:", output["text"]):
                    raise ValueError("발화 전용 출력 규칙 위반")
                checks = ["schema", "one_utterance", "no_start_or_close_directive"]
            result.update(status="ok", output=output, output_sha256=sha(canonical(output)), checks=checks)
        except ProviderError as exc:
            result.update(status="error", error_type=type(exc).__name__, error=str(exc))
        except ValueError:
            result.update(status="error", error_type="ValidationError", error="실제 출력 검증 실패")
        result["latency_seconds"] = round(time.monotonic() - started, 3)
        result["finished_at"] = utc_now()
        atomic_write(output_directory / (role + ".json"), json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        results.append(result)
        print(json.dumps({key: result[key] for key in ("role", "status", "latency_seconds", "finished_at")}, ensure_ascii=False), flush=True)
    summary = {**metadata, "finished_at": utc_now(), "attempts": len(results),
        "successes": sum(result["status"] == "ok" for result in results),
        "roles": [{key: result[key] for key in ("role", "status", "latency_seconds")} for result in results]}
    atomic_write(output_directory / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["successes"] == len(roles) else 1


if __name__ == "__main__":
    raise SystemExit(main())
