"""Allowlisted interview-time facts only. Never instantiate the server world loader."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from harness.models import Persona

ROOT = Path(__file__).resolve().parents[2]
FILES = ("history.json", "motivations.md", "behavior_patterns.md", "traits.md", "self_expression.json", "current_state.json")
EXPRESSION = ("voice", "self_expression", "memory_rule", "pressure_rule", "unknowns", "evidence_status")
CURRENT = {"current_career_goal", "push_factors", "pull_factors", "priority_hierarchy", "role_expectation",
           "autonomy_manager_expectation", "decision_context", "non_negotiables_tolerance", "major_current_uncertainties"}

def cleaned_markdown(text):
    lines = []
    for line in text.splitlines():
        if line.startswith(("HR-PERSONALITY", "근거:")):
            continue
        # The final trait paragraph also describes real conditional behavior.
        # Drop its file navigation sentence, never the whole paragraph.
        line = re.sub(r"구체적인 (?:조건과 )?규칙은 .*\[실행용 규칙\].*?에 있다\.", "", line)
        line = line.replace("본인의 E/K 경험에서 추출한", "본인의 경험에서 추출한")
        line = re.sub(r"P\d{2}-[REK]\d{2}\s*", "", line)
        line = re.sub(r"\s*—\s*(?:,\s*)*$", "", line)
        line = line.replace("K01~K04와 E05의 현재 조건을 함께 읽는다.", "")
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

def build(source, destination=None):
    source = Path(source).resolve()
    destination = (Path(destination) if destination else ROOT / "harness/resources/candidate_runtime").resolve()
    if destination.is_relative_to(source):
        raise ValueError("참고 원본은 읽기 전용입니다")
    pools = json.loads((ROOT / "harness/candidate_pools.json").read_text())
    ids = sorted({cid for group in pools.values() for cid in group})
    artifacts, entries = {}, {}
    for cid in ids:
        prefix = Path("web-server/models/worlds/corebridge-personality/candidates") / cid / "ground_truth"
        documents, sources = {}, []
        for filename in FILES:
            relative = prefix / filename
            expected = source / relative
            path = expected.resolve()
            if path != expected or not path.is_relative_to(source):
                raise ValueError("허용 목록 밖으로 연결되는 원본 경로")
            raw = path.read_bytes()
            documents[filename] = raw.decode("utf-8")
            sources.append({"path": relative.as_posix(), "sha256": hashlib.sha256(raw).hexdigest()})
        history = json.loads(documents["history.json"])
        if any(e["temporal_scope"] != "past_or_current_at_interview" for e in history["events"]):
            raise ValueError("미래 사건 반입 금지: " + cid)
        expression = json.loads(documents["self_expression.json"])
        current = json.loads(documents["current_state.json"])
        if (history["candidate_id"] != cid or expression["candidate_id"] != cid
                or current["candidate_id"] != cid or set(current["fields"]) != CURRENT
                or history["as_of"] != current["as_of"]):
            raise ValueError("후보/현재 상태 계약 불일치: " + cid)
        public = json.loads((ROOT / "harness/resources/materials/candidates" / cid / "application.json").read_text())
        value = {"id": cid, "name": public["name"], "as_of": current["as_of"],
            "history": [f["text"] for e in history["events"] for f in e["facts"]],
            "motivations": cleaned_markdown(documents["motivations.md"]),
            "behavior_patterns": cleaned_markdown(documents["behavior_patterns.md"]),
            "traits": cleaned_markdown(documents["traits.md"]),
            "self_expression": {k: expression[k] for k in EXPRESSION},
            "current_state": {k: {"state": f["state"], "certainty": {
                "DIRECT": "stated", "INFERRED": "tentative", "UNKNOWN": "unknown"}[f["evidence_type"]]}
                for k, f in current["fields"].items()}}
        Persona.model_validate(value, strict=True)
        raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        artifacts[cid] = raw
        entries[cid] = {"sources": sources, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    (destination / "personas").mkdir(parents=True, exist_ok=True)
    (destination / "__init__.py").write_text('"""Role-private interview-time persona resources."""\n')
    for cid, raw in artifacts.items(): (destination / "personas" / (cid + ".json")).write_text(raw, encoding="utf-8")
    (destination / "manifest.json").write_text(json.dumps({"version": "persona-import-2", "candidates": entries},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"과거·현재 후보 자료 {len(entries)}명 추출 완료")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    build(args.source, args.output)
