"""Synthetic persona imports and strict model input boundaries (no model calls)."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import candidate
from harness.storage import resource, sha
from scripts import import_candidate_personas as importer


IDS = ["P01", "P02", "P03", "P04", "P06", "P07", "P08", "P10"]
COMPANY = {key: "공개 자료" for key in ("company_intro", "job_posting", "culture_public")}
MESSAGE = {"id": "local-message-000001", "speaker": "interviewer", "text": "소개 부탁드립니다.", "at": 1800000000.0}


def context(role):
    if role == "candidate":
        persona = candidate.load_persona("P07")
        persona["resume"] = "공개 이력서"
        return {"persona": persona, "company_public": deepcopy(COMPANY), "scene": "인성면접",
                "transcript": [deepcopy(MESSAGE)], "closing_stage": "interview"}
    if role == "interviewer":
        return {"company": deepcopy(COMPANY), "candidate": {"id": "P07", "name": "오시온", "resume": "공개 이력서", "motivation": "공개 동기"},
                "insights": "사용자 기준", "messages": [deepcopy(MESSAGE)], "phase": "opening",
                "closing_stage": "interview", "remaining_seconds": 1700.0}
    return {"final_insights": "최종 기준", "company": deepcopy(COMPANY), "candidates": [{
        "candidate_id": "P07", "name": "오시온", "mode": "human", "duration_seconds": 1800.0,
        "insights_used": "당시 기준", "messages": [{**MESSAGE, "source_id": "P07:message:local-message-000001"}],
        "user_feedback": {"source_id": "P07:feedback", "text": "실제 사용자 평가"}, "protocol_note": None}],
        "evidence_sources": {"P07:feedback": {"candidate_id": "P07", "text": "실제 사용자 평가"}}}


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    for cid in IDS:
        directory = root / "web-server/models/worlds/corebridge-personality/candidates" / cid / "ground_truth"
        directory.mkdir(parents=True)
        docs = {
            "history.json": {"candidate_id": cid, "as_of": "2026-09-21", "events": [{"temporal_scope": "past_or_current_at_interview",
                "facts": [{"id": f"{cid}-E01-F01", "text": "자료 37건을 취합했다. 최종 결정은 다른 담당자가 했다."}]}]},
            "self_expression.json": {"candidate_id": cid, **{key: "설정된 말투와 기억" for key in importer.EXPRESSION}, "claims": ["DROP_METADATA"]},
            "current_state.json": {"candidate_id": cid, "as_of": "2026-09-21", "fields": {key: {
                "state": "아직 정하지 못했다." if key == "major_current_uncertainties" else "다음 역할을 생각한다.",
                "evidence_type": "UNKNOWN" if key == "major_current_uncertainties" else "INFERRED",
                "future_relevance": "DROP_METADATA", "evidence_refs": [f"{cid}-E01"], "confidence": "DROP_METADATA"}
                for key in importer.CURRENT}},
        }
        for name in importer.FILES:
            value = docs.get(name, f"# 현재의 생각\n\nHR-PERSONALITY-2.0 · candidate_private\n\n현재 배운 행동을 먼저 본다. 구체적인 규칙은 [실행용 규칙](../expected_behavior/behavioral_rules.json)에 있다.\n\n## {cid}-R01 선택 경향\n\n근거: {cid}-E01\n\n현재는 이직을 고려하지만 미래 결과는 모른다.")
            (directory / name).write_text(json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value, encoding="utf-8")
        (directory / "future_trajectory.json").write_text("DO_NOT_READ", encoding="utf-8")
    return root


def test_import_reads_only_allowlisted_files_and_preserves_facts(source, tmp_path, monkeypatch):
    original = Path.read_bytes
    reads = []

    def guarded(path):
        if path.is_relative_to(source):
            assert path.name in importer.FILES
            reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    output = tmp_path / "output"
    importer.build(source, output)
    assert len(reads) == len(IDS) * len(importer.FILES)
    manifest = json.loads((output / "manifest.json").read_text())
    assert sorted(manifest["candidates"]) == IDS
    for cid in IDS:
        raw = (output / "personas" / (cid + ".json")).read_text()
        persona = json.loads(raw)
        assert persona["history"] == ["자료 37건을 취합했다. 최종 결정은 다른 담당자가 했다."]
        assert "현재 배운 행동을 먼저 본다." in persona["traits"]
        assert persona["current_state"]["major_current_uncertainties"]["certainty"] == "unknown"
        assert persona["current_state"]["role_expectation"]["certainty"] == "tentative"
        assert not any(marker in raw for marker in ("DROP_METADATA", "DO_NOT_READ", "future_relevance", "evidence_refs", f"{cid}-E01", "expected_behavior", "candidate_private"))
        assert manifest["candidates"][cid]["sha256"] == sha(raw)
        assert all(not Path(entry["path"]).is_absolute() for entry in manifest["candidates"][cid]["sources"])


@pytest.mark.parametrize("change", ["future", "wrong_id", "wrong_date", "missing_file"])
def test_import_rejects_incomplete_or_mixed_source_without_partial_output(source, tmp_path, change):
    path = source / "web-server/models/worlds/corebridge-personality/candidates/P10/ground_truth/history.json"
    value = json.loads(path.read_text())
    if change == "future":
        value["events"][0]["temporal_scope"] = "future"
    elif change == "wrong_id":
        value["candidate_id"] = "P01"
    elif change == "wrong_date":
        value["as_of"] = "2040-01-01"
    if change == "missing_file":
        path.unlink()
    else:
        path.write_text(json.dumps(value))
    output = tmp_path / "output"
    with pytest.raises((ValueError, FileNotFoundError)):
        importer.build(source, output)
    assert not output.exists()


def test_import_does_not_follow_allowlisted_filename_to_another_source_file(source, tmp_path):
    directory = source / "web-server/models/worlds/corebridge-personality/candidates/P07/ground_truth"
    (directory / "history.json").unlink()
    try:
        (directory / "history.json").symlink_to("future_trajectory.json")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("이 Windows 계정에는 심볼릭 링크 생성 권한이 없습니다")
        raise
    with pytest.raises(ValueError, match="경로"):
        importer.build(source, tmp_path / "output")


def test_import_never_writes_into_reference_source(source):
    with pytest.raises(ValueError, match="읽기 전용"):
        importer.build(source, source / "new-output")


def test_bundle_requires_all_eight_and_valid_hash(monkeypatch):
    assert candidate.validate_bundle() == IDS
    actual = candidate.resource

    def missing(package, name):
        if package == "candidate_runtime" and name == "personas/P10.json":
            raise FileNotFoundError(name)
        return actual(package, name)

    monkeypatch.setattr(candidate, "resource", missing)
    with pytest.raises((ValueError, FileNotFoundError)):
        candidate.validate_bundle()
    monkeypatch.setattr(candidate, "resource", lambda package, name: actual(package, name) + " " if name == "personas/P07.json" else actual(package, name))
    with pytest.raises(ValueError, match="해시"):
        candidate.load_persona("P07")


@pytest.mark.parametrize("role", ["candidate", "interviewer", "ranker"])
def test_context_returns_independent_copy_and_rejects_top_level_leaks(role):
    value = context(role)
    validated = candidate.validate_context(role, value)
    assert validated == value and validated is not value
    value["unexpected"] = "private"
    assert "unexpected" not in validated
    with pytest.raises(ValueError):
        candidate.validate_context(role, value)


@pytest.mark.parametrize("role,path", [
    ("candidate", ["company_public"]), ("candidate", ["transcript", 0]),
    ("candidate", ["persona", "current_state"]), ("candidate", ["persona", "current_state", "role_expectation"]),
    ("candidate", ["persona", "self_expression"]), ("interviewer", ["company"]),
    ("interviewer", ["candidate"]), ("interviewer", ["messages", 0]),
    ("ranker", ["candidates", 0]), ("ranker", ["candidates", 0, "messages", 0]),
    ("ranker", ["candidates", 0, "user_feedback"]), ("ranker", ["evidence_sources", "P07:feedback"]),
])
def test_context_rejects_nested_leaks(role, path):
    value = context(role)
    nested = value
    for key in path:
        nested = nested[key]
    nested["persona_private"] = {"future_relevance": "forbidden"}
    with pytest.raises(ValueError):
        candidate.validate_context(role, value)


@pytest.mark.parametrize("role,path", [("candidate", ["scene"]), ("candidate", ["persona", "resume"]),
    ("interviewer", ["insights"]), ("interviewer", ["candidate", "resume"]),
    ("ranker", ["final_insights"]), ("ranker", ["candidates", 0, "protocol_note"])])
def test_context_rejects_objects_hidden_in_text_fields(role, path):
    value = context(role)
    nested = value
    for key in path[:-1]:
        nested = nested[key]
    nested[path[-1]] = {"private": "forbidden"}
    with pytest.raises(ValueError):
        candidate.validate_context(role, value)


def test_candidate_context_selects_current_candidate_and_drops_internal_message_fields():
    iv = {"candidate_id": "P07", "documents": {"resume": "이력서", "motivation": "지원동기"},
          "messages": [{**MESSAGE, "origin": "system", "key": "start", "insights": "secret"}],
          "insights": "SECRET", "feedback": "SECRET", "pool": "SECRET"}
    session = SimpleNamespace(state={"company": deepcopy(COMPANY), "interviews": [iv, {"candidate_id": "P01", "persona": "SECRET"}]})
    value = candidate.candidate_context(session, iv)
    assert value["persona"]["id"] == "P07"
    assert value["persona"]["resume"] == "이력서\n\n지원동기"
    assert value["transcript"] == [MESSAGE]
    assert "SECRET" not in json.dumps(value)
    assert "sha256" not in json.dumps(value)
    candidate.validate_context("candidate", value)


def test_persona_ids_outside_confirmed_bundle_are_not_loaded():
    for cid in ("P05", "P09", "../P07", "P07.json"):
        with pytest.raises(ValueError):
            candidate.load_persona(cid)


def test_runtime_fingerprint_changes_with_prompt(monkeypatch):
    before = candidate.runtime_fingerprint()
    actual = candidate.resource
    monkeypatch.setattr(candidate, "resource", lambda package, name: actual(package, name) + "\n새 지시" if package == "prompts" and name == "candidate.md" else actual(package, name))
    assert candidate.runtime_fingerprint() != before
