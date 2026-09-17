"""Four live Claude utterances in an isolated demo, with speech-only output."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
from types import SimpleNamespace

from harness.auth import ensure_claude_login
from harness.candidate import candidate_context, dialogue_view, runtime_fingerprint
from harness.models import CandidateTurn, InterviewTurn
from harness.provider import Claude, ProviderError
from harness.storage import atomic_write, canonical, resource, sha
from harness.terminal import message_line
from scripts.verify_role_models import INSIGHTS, company


def main():
    ensure_claude_login()
    provider = Claude("sonnet")
    cid = "P07"
    name = json.loads(resource("materials", f"candidates/{cid}/application.json"))["name"]
    iv = {"candidate_id": cid, "name": name, "documents": {
        "resume": resource("materials", f"candidates/{cid}/resume.txt"),
        "motivation": resource("materials", f"candidates/{cid}/support_motivation.md")},
        "messages": [], "closing_stage": "interview"}
    view = SimpleNamespace(state={"company": company()})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = Path(__file__).resolve().parents[1] / ".runtime/verification" / ("live-demo-" + stamp)
    folder.mkdir(parents=True, exist_ok=False)
    record = {"kind": "isolated-live-claude-demo", "model_requested": "sonnet",
        "runtime_sha256": runtime_fingerprint(), "calls": [], "status": "running",
        "cli_version": subprocess.run([provider.executable, "--version"], capture_output=True,
                                      text=True, check=True, timeout=10).stdout.strip()}
    lines = ["# 실제 Claude 대화 시연", "", "별도 시연이며 실제 면접·사용자 평가가 아닙니다.", ""]

    def save():
        atomic_write(folder / "transcript.md", "\n".join(lines) + "\n")
        atomic_write(folder / "metadata.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")

    def append(role, text):
        message = {"id": f"demo-message-{len(iv['messages']) + 1:06d}",
                   "speaker": role, "text": text, "at": time.time()}
        iv["messages"].append(message)
        line = message_line(iv, message)
        lines.extend([line, ""])
        save()
        print(line + "\n", flush=True)

    deadline = time.monotonic() + 180
    append("interviewer", f"{name}님 면접 시작하겠습니다")
    try:
        for number, role in enumerate(("interviewer", "candidate", "interviewer", "candidate"), 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("시연 시간이 만료되었습니다")
            if runtime_fingerprint() != record["runtime_sha256"]:
                raise ProviderError("시연 중 자료가 변경되었습니다")
            if role == "candidate":
                context, output_type = candidate_context(view, iv), CandidateTurn
            else:
                context = {"company": view.state["company"],
                    "candidate": {"id": cid, "name": name, **iv["documents"]},
                    "insights": INSIGHTS, "messages": dialogue_view(iv),
                    "phase": "opening" if number == 1 else "core",
                    "closing_stage": "interview", "remaining_seconds": float(remaining)}
                output_type = InterviewTurn
            start = time.monotonic()
            call = {"role": role, "status": "running", "input_sha256": sha(canonical(context)),
                    "prompt_sha256": sha(resource("prompts", role + ".md"))}
            record["calls"].append(call)
            save()
            result = provider.generate(role, context, output_type, timeout=min(60, remaining))
            if time.monotonic() >= deadline:
                raise ProviderError("시연 시간이 만료되었습니다")
            text = result["text"]
            if "수고하셨습니다" in text or "면접 시작하겠습니다" in text or re.search(r"(?m)^(면접관|지원자)\s*:", text):
                raise ProviderError("발화 전용 출력 검증 실패")
            call.update(status="ok", latency_seconds=round(time.monotonic() - start, 2))
            append(role, text)
        record["status"] = "complete"
    except (ProviderError, KeyboardInterrupt) as exc:
        record["status"] = "interrupted"
        record["error"] = str(exc) or "시연 취소"
        if record["calls"] and record["calls"][-1]["status"] == "running":
            record["calls"][-1]["status"] = "interrupted"
        print("[" + record["error"] + "]", flush=True)
    finally:
        provider.cancel()
        append("interviewer", "수고하셨습니다")
    return 0 if record["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
