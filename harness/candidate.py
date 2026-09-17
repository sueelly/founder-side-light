"""Explicit role views. Persona resources never enter interviewer or ranker views."""
from copy import deepcopy
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from .models import Persona
from .storage import canonical, resource, sha

CURRENT = {"current_career_goal", "push_factors", "pull_factors", "priority_hierarchy", "role_expectation",
           "autonomy_manager_expectation", "decision_context", "non_negotiables_tolerance", "major_current_uncertainties"}
ROLE_KEYS = {
    "candidate": {"persona", "company_public", "scene", "transcript", "closing_stage"},
    "interviewer": {"company", "candidate", "insights", "messages", "phase", "closing_stage", "remaining_seconds"},
    "ranker": {"final_insights", "company", "candidates", "evidence_sources", "validation_errors"},
}


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Company(_Input):
    company_intro: StrictStr
    job_posting: StrictStr
    culture_public: StrictStr


class _Message(_Input):
    id: StrictStr
    speaker: Literal["interviewer", "candidate"]
    text: StrictStr
    at: Annotated[float, Field(allow_inf_nan=False)]


class _CandidatePersona(Persona):
    resume: StrictStr

    @field_validator("current_state")
    @classmethod
    def complete_current_state(cls, value):
        if set(value) != CURRENT:
            raise ValueError("현재 상태 필드 불일치")
        return value


class _CandidateContext(_Input):
    persona: _CandidatePersona
    company_public: _Company
    scene: StrictStr
    transcript: list[_Message]
    closing_stage: StrictStr


class _PublicCandidate(_Input):
    id: StrictStr
    name: StrictStr
    resume: StrictStr
    motivation: StrictStr


class _InterviewerContext(_Input):
    company: _Company
    candidate: _PublicCandidate
    insights: StrictStr
    messages: list[_Message]
    phase: Literal["opening", "core", "wrap_up"]
    closing_stage: StrictStr
    remaining_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class _SourcedMessage(_Message):
    source_id: StrictStr


class _Feedback(_Input):
    source_id: StrictStr
    text: StrictStr


class _RankedInterview(_Input):
    candidate_id: StrictStr
    name: StrictStr
    mode: Literal["human", "agent"]
    duration_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    insights_used: StrictStr
    messages: list[_SourcedMessage]
    user_feedback: _Feedback
    protocol_note: StrictStr | None


class _EvidenceSource(_Input):
    candidate_id: StrictStr
    text: StrictStr


class _RankerContext(_Input):
    final_insights: StrictStr
    company: _Company
    candidates: list[_RankedInterview]
    evidence_sources: dict[StrictStr, _EvidenceSource]
    validation_errors: list[StrictStr] = Field(default_factory=list)


_CONTEXT_TYPES = {"candidate": _CandidateContext, "interviewer": _InterviewerContext, "ranker": _RankerContext}


def load_persona(cid):
    if not re.fullmatch(r"P(?:0[1-9]|10)", cid):
        raise ValueError("후보 ID 오류")
    manifest = json.loads(resource("candidate_runtime", "manifest.json"))["candidates"]
    if cid not in manifest:
        raise ValueError("후보 전용 자료 누락: " + cid)
    raw = resource("candidate_runtime", f"personas/{cid}.json")
    if sha(raw) != manifest[cid]["sha256"]:
        raise ValueError("후보 자료 해시 불일치: " + cid)
    value = Persona.model_validate_json(raw).model_dump()
    if value["id"] != cid or set(value["current_state"]) != CURRENT:
        raise ValueError("후보 자료 계약 불일치: " + cid)
    public = json.loads(resource("materials", f"candidates/{cid}/application.json"))
    if value["name"] != public["name"]:
        raise ValueError("공개 지원서 이름 불일치: " + cid)
    return value


def validate_bundle():
    from .allocation import load_pools
    ids = sorted({cid for group in load_pools().values() for cid in group})
    manifest = json.loads(resource("candidate_runtime", "manifest.json"))
    if set(manifest.get("candidates", {})) != set(ids):
        raise ValueError("확정 후보군 전체의 전용 자료가 필요합니다")
    for cid in ids:
        load_persona(cid)
        for name in ("resume.txt", "support_motivation.md"):
            resource("materials", f"candidates/{cid}/{name}")
    return ids


def runtime_fingerprint():
    ids = validate_bundle()
    values = {"personas": {cid: load_persona(cid) for cid in ids},
              "prompts": {r: resource("prompts", r + ".md") for r in ROLE_KEYS},
              "public": {cid: {f: resource("materials", f"candidates/{cid}/{f}")
                                  for f in ("resume.txt", "support_motivation.md", "application.json")} for cid in ids},
              "company": {n: resource("materials", f"company/{n}.md")
                          for n in ("company_intro", "job_posting", "culture_public")}}
    return sha(canonical(values))


def dialogue_view(iv):
    return [{key: m[key] for key in ("id", "speaker", "text", "at")} for m in iv["messages"]]


def candidate_context(session, iv):
    persona = load_persona(iv["candidate_id"])
    persona["resume"] = iv["documents"]["resume"] + "\n\n" + iv["documents"]["motivation"]
    return validate_context("candidate", {"persona": persona, "company_public": session.state["company"],
        "scene": f"{persona['as_of']} 시점의 가상 한국어 인성면접. 서류와 기술면접은 통과했다.",
        "transcript": dialogue_view(iv), "closing_stage": iv.get("closing_stage", "interview")})


def validate_context(role, context):
    schema = _CONTEXT_TYPES.get(role)
    if schema is None:
        raise ValueError("역할별 입력 범위 불일치")
    # Validate every nested record before crossing the subprocess boundary. In
    # particular, a dict in a nominal text field must not become serialized secrets.
    schema.model_validate(context, strict=True)
    return deepcopy(context)
