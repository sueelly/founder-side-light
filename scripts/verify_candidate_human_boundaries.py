"""Repeat two observed follow-ups with identical input and the revised prompt."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from types import SimpleNamespace

from harness.candidate import candidate_context, runtime_fingerprint
from harness.models import CandidateTurn
from harness.provider import Claude, ProviderError
from harness.storage import canonical, resource, sha

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".runtime/verification/candidate-human-v2/targeted-eight"
OUT = ROOT / ".runtime/verification/candidate-human-v2/boundary-followups"


def check(cid, runtime_sha):
    previous = json.loads((BASE / f"{cid}.json").read_text())
    messages = previous["messages"][:6]
    assert messages[-1]["speaker"] == "interviewer"
    session = SimpleNamespace(state={"company": {
        name: resource("materials", f"company/{name}.md")
        for name in ("company_intro", "job_posting", "culture_public")}})
    iv = {"candidate_id": cid, "documents": {
        "resume": resource("materials", f"candidates/{cid}/resume.txt"),
        "motivation": resource("materials", f"candidates/{cid}/support_motivation.md")},
        "messages": messages, "closing_stage": "interview"}
    context = candidate_context(session, iv)
    input_sha = sha(canonical(context))
    assert input_sha == previous["calls"][2]["input_sha256"]
    assert runtime_fingerprint() == runtime_sha
    result = {"kind": "synthetic-development-followup-with-real-claude",
        "candidate_id": cid, "input_sha256": input_sha, "input_identical_to_previous": True,
        "runtime_sha256": runtime_sha, "candidate_prompt_sha256": sha(resource("prompts", "candidate.md")),
        "model_requested": "sonnet", "automatic_retry": False,
        "question": messages[-1]["text"], "previous_response": previous["messages"][6]["text"]}
    started = time.monotonic()
    try:
        result["response"] = Claude("sonnet").generate("candidate", context, CandidateTurn, timeout=90)["text"]
        result["status"] = "ok"
    except ProviderError as exc:
        result.update(status="error", error=str(exc))
    result["latency_seconds"] = round(time.monotonic() - started, 3)
    (OUT / f"{cid}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    runtime_sha = runtime_fingerprint()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda cid: check(cid, runtime_sha), ("P06", "P08")))
    return 0 if all(item["status"] == "ok" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
