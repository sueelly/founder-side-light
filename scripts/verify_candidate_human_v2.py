"""Eight bounded, real-Claude candidate-prompt checks in an isolated copy.

Run only after the candidate prompt and environment are ready. This is a
development conversation, not a user interview, assessment, or ranking. Only
final speech and sanitized execution metadata are written; no input persona,
allocation, credentials, raw CLI envelope, or user criteria are serialized.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import platform
import re
import subprocess

from scripts import verify_candidate_quality as quality


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".runtime/verification/candidate-human-v2"
CASES = {
    "P08": (
        "보고 방식·사실 구체화·미확인과 미행동 구분·압박 뒤 재량 선호",
        "아직 일이 다 끝나지 않았을 때 대표나 동료에게 진행 상황을 보통 어떻게 알려 주시나요?",
        "갱신 계약의 첨부 여섯 개 중 두 개를 못 받은 일에서, 대표와 동료에게 각각 언제 무엇을 알렸는지 구체적으로 말씀해 주세요. 당시에는 왜 그렇게 판단하셨나요?",
        "9월 9일 인계 목록을 공유한 뒤 아직 완료 확인을 받지 못했다면, 그 뒤에는 동료에게 후속 연락을 한 번도 하지 않으신 거네요? 실제로 어디까지 확인할 수 있는지 말씀해 주세요.",
        "그럼 대표께 필요한 내용을 직접 판단해 추려 드리던 방식 자체를 포기하고, 사소한 진행도 전부 수시로 공유해야 한다는 뜻인가요? 앞으로 본인 재량은 중요하지 않은 건가요?",
    ),
    "P06": (
        "넓은 개선 경험·요청 범위 구체화·원칙을 새 사건으로 바꾸지 않기·조건 압박",
        "반복해서 들어오는 자료 요청을 더 수월하게 처리하려고 바꿔 본 경험이 있나요? 편하게 말씀해 주세요.",
        "2024년 외부 검토 요청에서는 몇 건을 맡았고, 매일 17시 공유 때 무엇을 남겼나요? 제출 계획을 정리할 때까지 남은 항목은 어떻게 처리했는지도 말씀해 주세요.",
        "미완료 두 건도 일단 완료로 표시하자는 주변 의견이 실제로 있었던 건가요? 누가 무슨 말을 했고 본인은 뭐라고 답했는지, 실제로 있었던 일로 설명해 주세요.",
        "저희는 반복 자료의 처리량이 계속 늘어날 수 있고, 개선 제안을 검토할 시간이나 통로는 약속드릴 수 없어요. 그래도 개선은 개인적으로 알아서 하면 되니 이 조건도 문제없다고 볼 수 있나요?",
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="새 실행 이름; 기존 결과 덮어쓰기 금지")
    parser.add_argument("--expected-runtime-sha", required=True,
                        help="준비 완료된 복사본의 고정 runtime SHA-256")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", args.run_id):
        parser.error("run-id는 영문 소문자·숫자·하이픈만 사용합니다")
    if not re.fullmatch(r"[0-9a-f]{64}", args.expected_runtime_sha):
        parser.error("expected-runtime-sha는 SHA-256이어야 합니다")
    actual_sha = quality.runtime_fingerprint()
    if actual_sha != args.expected_runtime_sha:
        raise ValueError("준비 완료 시점과 실행 자료가 다릅니다; 모델을 호출하지 않습니다")

    # The shared helper only uses this mapping for the development questions.
    # Its candidate_context / provider boundary is the same as the application.
    quality.CASES = CASES
    output = OUTPUT / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    executable = quality.Claude("sonnet").executable
    version = subprocess.run([executable, "--version"], capture_output=True, text=True,
                             timeout=10, check=True).stdout.strip()
    metadata = {
        "model_requested": "sonnet", "model_resolved": "not exposed by provider",
        "cli_version": version, "python": platform.python_version(),
        "platform": platform.system(), "runtime_sha256": actual_sha,
        "candidate_prompt_sha256": quality.sha(quality.resource("prompts", "candidate.md")),
        "verification_script_sha256": quality.sha(Path(__file__).read_text(encoding="utf-8")),
        "automatic_retry": False, "max_calls_per_candidate": 4,
        "maximum_calls_total": 8,
    }
    quality.atomic_write(output / "metadata.json",
                         json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(quality.run_case, cid, metadata, output, 4) for cid in CASES]
        results = [future.result() for future in as_completed(futures)]
    summary = {
        **metadata, "finished_at": quality.utc_now(), "candidate_ids": list(CASES),
        "kind": "synthetic-development-questions-with-real-claude",
        "planned_calls": 8, "attempts": sum(len(item["calls"]) for item in results),
        "successes": sum(call["status"] == "ok" for item in results for call in item["calls"]),
        "max_parallel_calls": 2, "within_candidate_execution": "sequential",
        "quality_verdict": "manual-review-required",
    }
    quality.atomic_write(output / "summary.json",
                         json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["successes"] == summary["planned_calls"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
