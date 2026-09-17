"""Bounded, real-model development checks; never a user interview/evaluation.

Run smoke first, inspect its speech, then remaining. Results contain only final
speech and sanitized metadata, not personas, raw CLI envelopes or credentials.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import subprocess
import time
from types import SimpleNamespace

from harness.candidate import candidate_context, runtime_fingerprint
from harness.models import CandidateTurn
from harness.provider import Claude, ProviderError
from harness.storage import atomic_write, canonical, resource, sha


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / ".runtime/verification/candidate-quality"
GROUPS = {"smoke": ("P07", "P03"), "remaining": ("P06", "P01", "P02", "P04", "P08", "P10"),
          "refined": ("P01", "P06", "P02")}
CASES = {
    "P07": ("넓은 경험 질문 → 수치의 잘못된 전제",
        "보고 자료를 준비하는 방식을 바꿔 본 경험이 있다면 편하게 말씀해 주세요.",
        "30% 단축이면 본인이 직접 작업한 시간도 매번 30% 줄었다는 뜻인가요? 비교한 범위를 조금 더 설명해 주세요."),
    "P03": ("넓은 판단 경험 → 현재의 자율성에 대한 잘못된 전제",
        "처음 업무를 익힐 때 스스로 판단하기 어려웠던 경험이 있나요? 어떻게 지내셨는지 말씀해 주세요.",
        "그러면 지금도 파일 이름 같은 작은 일까지 매번 승인을 받아야 하는 편인가요? 이후에 직접 결정했던 일을 하나만 말씀해 주세요."),
    "P06": ("넓은 오류 대응 경험 → 압박과 시기 구분",
        "본인이 한 일에 오류가 있다는 지적을 받았던 경험과 그때의 대응을 말씀해 주세요.",
        "처음부터 바로 본인 잘못을 인정했고 그 뒤로는 같은 오류가 없었다고 이해해도 될까요? 초기에 있었던 일과 나중에 있었던 일을 나눠 주세요."),
    "P01": ("넓은 개선 경험 → 근거 없는 정확한 수치",
        "반복해서 생기는 자료 문제를 줄이려고 직접 바꿔 본 경험이 있나요? 어떤 일을 하셨나요?",
        "그 체크리스트로 회사 전체 오류율이 정확히 몇 퍼센트 줄었나요? 측정한 숫자와 실제 확인한 범위를 구분해 말씀해 주세요."),
    "P02": ("넓은 동기 → 회사 조건과 장기 약속 압박",
        "다음 회사에서는 어떤 일을 더 해 보고 싶으세요? 지금 그런 생각을 하시는 이유도 궁금합니다.",
        "저희는 분석 업무에 쓸 시간을 별도로 약속할 수 없어요. 그래도 이 역할에서 오래 일하겠다고 확답할 수 있으신가요?"),
    "P04": ("넓은 담당 범위 → 실제 전문 판단과 자기 설명 대조",
        "최근 투자 실사에서 어떤 역할을 맡으셨는지 본인 말로 설명해 주세요.",
        "법무 쪽에서 계약 쟁점 네 건을 검토하고 대표가 투자 조건을 협상했다면, 전문 판단까지 직접 단독으로 맡으셨다는 뜻은 아닌가요? 본인이 한 범위를 구분해 주세요."),
    "P08": ("넓은 보고 방식 → 미완료 보고의 구체적 대조",
        "아직 일이 다 끝나지 않았을 때 경영진이나 동료에게 진행 상황을 어떻게 알려 주는 편인가요?",
        "첨부 여섯 개 중 두 개를 못 받은 상태에서 완료라고 보고한 일은 어떻게 보세요? 단순히 설명을 짧게 한 것과 어떤 차이가 있었나요?"),
    "P10": ("넓은 공백 설명 → 유급 경력 전제와 기간 구분",
        "2024년에 다음 회사에 들어가기 전까지 어떤 시간을 보내셨나요? 편하게 말씀해 주세요.",
        "그러면 그 일곱 달 내내 돈을 받고 프로젝트를 하신 건가요? 휴식과 구직, 학습, 샘플을 만든 기간을 나눠서 말씀해 주세요."),
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def run_case(cid, metadata, output, max_turns=2):
    destination = output / (cid + ".json")
    if destination.exists():
        raise ValueError("기존 검증 결과는 덮어쓰지 않습니다: " + cid)
    topic, *questions = CASES[cid]
    persona_name = json.loads(resource("materials", f"candidates/{cid}/application.json"))["name"]
    iv = {"candidate_id": cid, "documents": {
        "resume": resource("materials", f"candidates/{cid}/resume.txt"),
        "motivation": resource("materials", f"candidates/{cid}/support_motivation.md")},
        "messages": [], "closing_stage": "interview"}
    session = SimpleNamespace(state={"company": {name: resource("materials", f"company/{name}.md")
        for name in ("company_intro", "job_posting", "culture_public")}})
    # Synthetic conversation only. No Session.create, user criteria, feedback,
    # allocation or final ranking is created or accepted by this script.
    def append(speaker, text):
        iv["messages"].append({"id": f"local-message-{len(iv['messages']) + 1:06d}",
            "speaker": speaker, "text": text, "at": time.time()})

    append("interviewer", f"{persona_name}님 면접 시작하겠습니다")
    provider = Claude("sonnet")
    result = {**metadata, "candidate_id": cid, "topic": topic, "started_at": utc_now(),
        "kind": "synthetic-development-questions-with-real-claude", "calls": [], "messages": []}
    for number, question in enumerate(questions[:max_turns], 1):
        if runtime_fingerprint() != metadata["runtime_sha256"]:
            raise ValueError("검증 중 실행 자료가 바뀌었습니다")
        append("interviewer", question)
        context = candidate_context(session, iv)
        started = time.monotonic()
        call = {"number": number, "started_at": utc_now(), "input_sha256": sha(canonical(context))}
        try:
            response = provider.generate("candidate", context, CandidateTurn, timeout=90)
            append("candidate", response["text"])
            call.update(status="ok", characters=len(response["text"]), response_sha256=sha(response["text"]))
        except ProviderError as exc:
            # ProviderError intentionally contains only sanitized messages.
            call.update(status="error", error_type=type(exc).__name__, error=str(exc))
        call["latency_seconds"] = round(time.monotonic() - started, 3)
        result["calls"].append(call)
        result["messages"] = iv["messages"]
        result["updated_at"] = utc_now()
        atomic_write(destination, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"candidate": cid, "call": number, **call}, ensure_ascii=False), flush=True)
        if call["status"] != "ok":
            break
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=GROUPS)
    parser.add_argument("--run-id", default="", help="새 검증 묶음의 짧은 이름; 기존 결과는 보존")
    args = parser.parse_args()
    if args.run_id and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", args.run_id):
        raise ValueError("run-id는 영문 소문자·숫자·하이픈만 사용합니다")
    output = OUTPUT / args.run_id if args.run_id else OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    targets = GROUPS[args.group]
    turn_limits = {cid: 1 if args.group == "refined" and cid in {"P01", "P06"} else 2 for cid in targets}
    if any((output / (cid + ".json")).exists() for cid in targets):
        raise ValueError("이미 실행한 그룹입니다. 결과를 먼저 검토하세요; 자동 재시도하지 않습니다")
    if args.group == "remaining":
        for cid in GROUPS["smoke"]:
            previous = json.loads((output / (cid + ".json")).read_text(encoding="utf-8"))
            if len(previous["calls"]) != 2 or any(call["status"] != "ok" for call in previous["calls"]):
                raise ValueError("먼저 smoke 그룹의 두 발화가 모두 성공해야 합니다")
    executable = Claude("sonnet").executable
    version = subprocess.run([executable, "--version"], capture_output=True, text=True,
                             timeout=10, check=True).stdout.strip()
    metadata = {"model_requested": "sonnet", "model_resolved": "not exposed by provider",
        "cli_version": version, "python": platform.python_version(), "platform": platform.system(),
        "runtime_sha256": runtime_fingerprint(), "candidate_prompt_sha256": sha(resource("prompts", "candidate.md")),
        "automatic_retry": False, "max_calls_per_candidate": 2}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_case, cid, metadata, output, turn_limits[cid]) for cid in targets]
        results = [future.result() for future in as_completed(futures)]
    summary = {"group": args.group, "finished_at": utc_now(), "candidate_ids": list(targets),
        "planned_calls": sum(turn_limits.values()),
        "attempts": sum(len(result["calls"]) for result in results),
        "successes": sum(call["status"] == "ok" for result in results for call in result["calls"]),
        "max_parallel_calls": 2, **metadata}
    atomic_write(output / (args.group + "-summary.json"), json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["successes"] == summary["planned_calls"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
