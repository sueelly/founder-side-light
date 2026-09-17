"""Local, evidence-checked ranking. No HTTP transport or submission credentials."""
from .candidate import dialogue_view
from .models import Ranking
from .storage import canonical, sha


def ranking_context(session):
    session.check_integrity()
    state = session.state
    if len(state["interviews"]) != 4 or any(iv["status"] != "completed" for iv in state["interviews"]):
        raise ValueError("네 명의 면접 종료와 사용자 평가가 모두 필요합니다")
    if sha(state["final_insights"]) != state["interviews"][-1]["insights_after_sha256"]:
        raise ValueError("최종 기준 해시가 네 번째 확정 기준과 다릅니다")
    candidates, sources = [], {}
    for iv in state["interviews"]:
        cid = iv["candidate_id"]
        transcript = []
        for message in dialogue_view(iv):
            source_id = f"{cid}:message:{message['id']}"
            transcript.append({**message, "source_id": source_id})
            if message["speaker"] == "candidate":
                sources[source_id] = {"candidate_id": cid, "text": message["text"]}
        feedback_id = f"{cid}:feedback"
        sources[feedback_id] = {"candidate_id": cid, "text": iv["feedback"]}
        candidates.append({"candidate_id": cid, "name": iv["name"], "mode": iv["mode"],
            "duration_seconds": iv["closed_at"] - iv["started_at"], "insights_used": iv["insights"],
            "messages": transcript, "user_feedback": {"source_id": feedback_id, "text": iv["feedback"]},
            "protocol_note": iv.get("protocol_note")})
    return {"final_insights": state["final_insights"], "company": state["company"],
            "candidates": candidates, "evidence_sources": sources}, sources


def validate_ranking(result, candidates, sources):
    ranking = Ranking.model_validate(result).model_dump()["ranking"]
    if {item["candidate_id"] for item in ranking} != set(candidates):
        raise ValueError("면접한 네 명을 한 번씩 순위에 포함해야 합니다")
    if {item["rank"] for item in ranking} != {1, 2, 3, 4}:
        raise ValueError("순위는 중복 없이 1~4여야 합니다")
    for item in ranking:
        for evidence in item["evidence"]:
            source = sources.get(evidence["source_id"])
            if not source or source["candidate_id"] != item["candidate_id"]:
                raise ValueError("해당 후보의 발언 또는 사용자 평가만 인용할 수 있습니다")
            if evidence["quote"] not in source["text"]:
                raise ValueError("인용이 실제 원문과 일치하지 않습니다")
    return sorted(ranking, key=lambda item: item["rank"])


def prepare_final(session, provider):
    session.check_integrity()
    if session.state.get("final_result"):
        return session.state["final_result"]
    context, sources = ranking_context(session)
    session.validate_runtime()
    errors = []
    for _ in range(3):
        value = provider.generate("ranker", {**context, "validation_errors": errors}, Ranking, timeout=120)
        try:
            ranking = validate_ranking(value, [iv["candidate_id"] for iv in session.state["interviews"]], sources)
            break
        except ValueError as exc:
            errors.append(str(exc))
    else:
        raise ValueError("순위 근거 검증에 실패했습니다. finalize로 다시 시도하세요")
    result = {"schema_version": "founder-terminal-result-1", "session_id": session.state["session_id"],
        "insights_markdown": session.state["final_insights"], "insights_sha256": sha(session.state["final_insights"]),
        "ranking": ranking, "interviews": [{
            **{k: iv[k] for k in ("number", "candidate_id", "name", "mode", "started_at", "closed_at", "limit_seconds",
                                  "insight_action", "insights_after_sha256", "feedback_sha256")},
            "insights_before_sha256": iv["insights_sha256"], "transcript_sha256": sha(canonical(iv["messages"])),
            "protocol_note": iv.get("protocol_note")} for iv in session.state["interviews"]]}
    result["result_sha256"] = sha(canonical(result))
    session.state.update(final_result=result, phase="complete")
    session.save()
    return result
