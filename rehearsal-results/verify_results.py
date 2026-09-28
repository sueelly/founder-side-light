#!/usr/bin/env python3
"""Read-only, stdlib rehearsal verification; writes only verification.json.

Run after finalization: python rehearsal-results/verify_results.py
Independent synthetic tests: python rehearsal-results/verify_results.py --self-test
For four completed interviews awaiting ranking, explicitly pass
--allow-incomplete-finalization (distinct status and exit code 2).
No harness imports, model calls, repairs, persona parsing, or credential access.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import tempfile


class Invalid(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def file_digest(path):
    # Baseline resources are never decoded, parsed, or printed.
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def inside(root, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and ".." not in relative.parts, "허용되지 않은 상대 경로")
    path = root / relative
    require(path.resolve().is_relative_to(root.resolve()), "검증 범위를 벗어난 경로")
    return path


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def expected_outputs(state):
    """Independent reconstruction of committed, read-only documents."""
    result = {}
    for iv in state["interviews"]:
        prefix = f"{iv['number']:02d}-{iv['candidate_id']}/"
        lines = [f"# {iv['name']} ({iv['candidate_id']}) 면접 원문", ""]
        for message in iv["messages"]:
            label = f"지원자({iv['name']})" if message["speaker"] == "candidate" else "면접관"
            lines.extend([f"## {message['id']} · {label} · {message['at']}", "", message["text"], ""])
        result[prefix + "transcript.md"] = "\n".join(lines)
        result[prefix + "insights.before.md"] = iv["insights"]
        result[prefix + "insights.after.md"] = iv["insights_after"]
        result[prefix + "feedback.confirmed.md"] = iv["feedback"]
    if "final_result" not in state:
        return result
    value = state["final_result"]
    result["ranking.json"] = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    lines = ["# 최종 후보 순위", "", "네 번째 사용자 피드백 이후의 최종 기준을 적용했습니다.", ""]
    for item in value["ranking"]:
        lines.extend([f"## {item['rank']}위 · {item['candidate_id']}", "", item["rationale"], "",
                      "확인되지 않은 점: " + item["uncertainty"], ""])
        lines.extend(f"- {e['source_id']}: {e['quote']}" for e in item["evidence"])
        lines.append("")
    result["ranking.md"] = "\n".join(lines)
    return result


def interview_summary(iv):
    return {**{key: iv[key] for key in (
        "number", "candidate_id", "name", "mode", "started_at", "closed_at", "limit_seconds",
        "insight_action", "insights_after_sha256", "feedback_sha256")},
        "insights_before_sha256": iv["insights_sha256"],
        "transcript_sha256": digest(canonical(iv["messages"])), "protocol_note": iv.get("protocol_note")}


def verify(root, *, allow_incomplete_finalization=False):
    root = Path(root).resolve()
    report = {"kind": "independent-rehearsal-record-verification", "verified_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed", "checks": [], "warnings": [], "interviews": [],
        "limitations": [
            "사람 역할의 질문·기준·평가는 사용자 허가에 따른 에이전트 리허설 입력이며 실제 사용자의 판단이 아니다.",
            "저장된 결과와 기준 파일의 일관성을 검증한다. 실제 호출·입력 행위나 내용의 진실성·대화 품질을 독립적으로 증명하지 않는다.",
            "해시는 서명이 아니다. REHEARSAL-NOTICE의 기준 목록 자체를 신뢰하며 목록 밖의 파일은 동일성을 검증하지 않는다.",
            "인용은 해당 후보 발언·확정 평가의 정확한 연속 부분문자열이어야 한다. 순위 설명의 의미적 타당성은 별도 검토가 필요하다.",
            "일반 발화에는 마감 이후 기록을 허용하지 않는다. 종료 기록 지연은 protocol_note와 함께 별도 보고한다.",
        ]}
    def checked(name):
        report["checks"].append(name)
    try:
        notice_path = root / "REHEARSAL-NOTICE.json"
        notice_bytes = notice_path.read_bytes()
        notice = json.loads(notice_bytes)
        require(notice.get("kind") == "user-authorized-agent-as-human-rehearsal", "리허설 고지 유형 불일치")
        require(Path(notice["copy"]).resolve() == root, "고지의 복사본 경로 불일치")
        require(notice.get("copied_entire_tree") is True and notice.get("code_modified") is False,
                "코드 무변경 리허설 고지 불일치")
        source = Path(notice["source"]).resolve()
        require(source != root and source.is_dir(), "원본 경로 확인 실패")
        session = inside(root, notice["session_path"])
        state_bytes = (session / "state.json").read_bytes()
        state = json.loads(state_bytes)
        incomplete = state.get("phase") == "finalizing" and allow_incomplete_finalization
        require(state.get("phase") == "complete" or incomplete,
                "phase가 complete가 아니다. 네 번째 평가와 최종 순위를 완료한 뒤 실행하세요")
        if incomplete:
            require("final_result" not in state, "순위 대기 상태에 final_result가 존재한다")
            require(not any((session / name).exists() or (session / name).is_symlink()
                            for name in ("ranking.json", "ranking.md")), "순위 대기 상태에 순위 파일이 존재한다")
        report["finalization_complete"] = not incomplete
        require(state.get("schema_version") == "founder-terminal-1", "지원하지 않는 세션 형식")
        require(state.get("state_sha256") == digest(canonical({k: v for k, v in state.items() if k != "state_sha256"})),
                "상태 SHA256 불일치")
        report.update(session_id=state["session_id"], phase=state["phase"], session_state_sha256=state["state_sha256"],
            state_file_sha256=digest(state_bytes), notice_sha256=digest(notice_bytes), runtime_sha256=state.get("runtime_sha256"))
        checked("finalizing_phase_and_state_sha256" if incomplete else "complete_phase_and_state_sha256")

        baseline = notice["source_files_sha256"]
        require(isinstance(baseline, dict) and bool(baseline), "기준 파일 SHA 목록 누락")
        baseline_results = []
        for name, expected in sorted(baseline.items()):
            require(Path(name).parts[0] in {"harness", "candidate_runtime", "materials", "prompts", "templates"},
                    "기준 목록에 허용 범위 밖의 파일이 있다")
            require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "기준 SHA 형식 오류")
            original_hash = file_digest(inside(source, name))
            copy_hash = file_digest(inside(root, name))
            require(original_hash == expected and copy_hash == expected, "원본·복사본·기준 SHA 불일치: " + name)
            baseline_results.append({"path": name, "sha256": expected, "source_matches": True, "copy_matches": True})
        report["baseline"] = {"files_checked": len(baseline_results), "files": baseline_results}
        checked("notice_baseline_source_and_copy_byte_hashes")

        interviews = state["interviews"]
        require(isinstance(interviews, list) and len(interviews) == 4, "면접 수가 4명이 아니다")
        ids = [iv["candidate_id"] for iv in interviews]
        require(len(set(ids)) == 4 and ids[0] == "P07", "후보 중복 또는 첫 후보 불일치")
        require(all(isinstance(cid, str) and re.fullmatch(r"P(?:0[1-9]|10)", cid) for cid in ids), "후보 ID 형식 오류")
        sources = {}
        for index, iv in enumerate(interviews, 1):
            cid, label = iv["candidate_id"], f"{index}번 면접"
            mode, limit = ("human", 1800) if index == 1 else ("agent", 1200)
            require(iv["number"] == index and iv["mode"] == mode and iv["status"] == "completed", label + " 순서·역할·완료 상태 오류")
            require(type(iv["limit_seconds"]) is int and iv["limit_seconds"] == limit, label + " 제한 시간 변경")
            start, deadline, close = (iv[k] for k in ("started_at", "deadline_at", "closed_at"))
            require(all(finite(t) for t in (start, deadline, close)), label + " 시각 오류")
            require(abs(deadline - start - limit) < .001 and close >= start, label + " 고정 마감 오류")
            require(index == 1 or start >= interviews[index - 2]["closed_at"], label + " 앞 면접 종료보다 먼저 시작했다")
            require(iv.get("next_role") is None and (not iv.get("turn") or iv["turn"]["status"] not in {"running", "retry"}), label + " 미완료 차례")
            before, after = digest(iv["insights"]), digest(iv["insights_after"])
            require(before == iv["insights_sha256"] and after == iv["insights_after_sha256"], label + " 기준 SHA 불일치")
            require(digest(iv["feedback"]) == iv["feedback_sha256"] and bool(iv["feedback"].strip()), label + " 확정 평가 SHA 오류")
            require(iv["insight_action"] == ("unchanged" if before == after else "updated"), label + " 기준 변경 선택 오류")
            confirmation = iv["insights_confirmation"]
            require(confirmation["sha256"] == before and finite(confirmation["at"]) and confirmation["at"] <= start,
                    label + " 시작 기준 확인 오류")
            messages = iv["messages"]
            require(isinstance(messages, list) and len(messages) >= 3, label + " 발화 누락")
            require(messages[0]["key"] == "start" and messages[0]["speaker"] == "interviewer"
                    and messages[0]["text"] == f"{iv['name']}님 면접 시작하겠습니다", label + " 시작 문구 오류")
            require(messages[-1]["key"] == "close" and messages[-1]["speaker"] == "interviewer"
                    and messages[-1]["text"] == "수고하셨습니다" and messages[-1]["at"] == close, label + " 종료 문구·시각 오류")
            message_ids = [m["id"] for m in messages]
            require(len(set(message_ids)) == len(messages) and len({m["key"] for m in messages}) == len(messages), label + " 발화 ID·키 중복")
            require(message_ids == [f"local-message-{i:04d}" for i in range(1, len(messages) + 1)], label + " 로컬 발화 ID 순서 오류")
            require(sum(m["text"] == "수고하셨습니다" for m in messages) == 1, label + " 종료 중복")
            require(sum(m["text"] == messages[0]["text"] for m in messages) == 1, label + " 시작 중복")
            previous = start
            for number, message in enumerate(messages):
                at = message["at"]
                require(finite(at) and previous <= at <= close, label + " 발화 시각이 단조 증가하지 않는다")
                require(message["speaker"] in {"interviewer", "candidate"} and isinstance(message["text"], str)
                        and bool(message["text"].strip()), label + " 화자·본문 형식 오류")
                require(number == len(messages) - 1 or at < deadline, label + " 마감 이후 일반 발화")
                require(message["origin"] in {"agent", "human", "system"}, label + " 발화 출처 오류")
                if message["speaker"] == "candidate":
                    require(message["origin"] == "agent", label + " 지원자 출처 오류")
                    sources[f"{cid}:message:{message['id']}"] = (cid, message["text"])
                elif mode == "agent":
                    require(message["origin"] != "human", label + " AI 면접에 사람 발화 혼입")
                previous = at
            candidate_messages = [m for m in messages if m["speaker"] == "candidate"]
            lengths = [len(m["text"]) for m in candidate_messages]
            require(bool(lengths), label + " 지원자 응답 없음")
            interviewer_agent = sum(m["speaker"] == "interviewer" and m["origin"] == "agent" for m in messages)
            interviewer_human = sum(m["speaker"] == "interviewer" and m["origin"] == "human" for m in messages)
            require(interviewer_human > 0 if mode == "human" else interviewer_agent > 0, label + " 면접관 입력 기록 없음")
            require(mode != "human" or interviewer_agent == 0, label + " 사람 면접관 질문을 AI가 대체했다")
            lateness = max(0, close - deadline)
            require(not lateness or (isinstance(iv.get("protocol_note"), str) and bool(iv["protocol_note"].strip())),
                    label + " 지연 종료의 protocol_note 누락")
            if lateness:
                report["warnings"].append({"candidate_id": cid, "kind": "documented_closing_delay", "seconds": round(lateness, 6)})
            sources[f"{cid}:feedback"] = (cid, iv["feedback"])
            report["interviews"].append({"number": index, "candidate_id": cid, "mode": mode,
                "started_at": start, "deadline_at": deadline, "closed_at": close,
                "duration_seconds": round(close - start, 6), "limit_seconds": limit,
                "closing_delay_seconds": round(lateness, 6), "utterances": len(messages),
                "candidate_utterances": len(lengths), "interviewer_utterances": len(messages) - len(lengths),
                "human_interviewer_utterances": interviewer_human, "agent_interviewer_utterances": interviewer_agent,
                "candidate_response_characters": {"unit": "Unicode code points, including whitespace",
                    "min": min(lengths), "max": max(lengths), "mean": round(statistics.mean(lengths), 2),
                    "median": statistics.median(lengths), "total": sum(lengths)},
                "insights_before_sha256": before, "insights_after_sha256": after,
                "feedback_sha256": iv["feedback_sha256"], "transcript_sha256": digest(canonical(messages))})
        checked("four_interviews_modes_fixed_deadlines_utterances_and_confirmed_snapshots")

        require(state["final_insights"] == interviews[-1]["insights_after"]
                and digest(state["final_insights"]) == interviews[-1]["insights_after_sha256"], "네 번째 최종 기준 불일치")
        report["final_insights_sha256"] = interviews[-1]["insights_after_sha256"]
        if incomplete:
            report["ranking_verification"] = {"status": "skipped", "reason": "네 명 평가 완료, 최종 순위 생성 미완료",
                "checks_skipped": ["final_result_hash", "ranking_files", "unique_ranks", "ranking_evidence"]}
            report["warnings"].append({"kind": "finalization_incomplete", "final_result_present": False,
                                       "ranking_files_present": False})
        else:
            result = state["final_result"]
            require(result["schema_version"] == "founder-terminal-result-1" and result["session_id"] == state["session_id"], "최종 결과 세션 오류")
            require(result["result_sha256"] == digest(canonical({k: v for k, v in result.items() if k != "result_sha256"})), "최종 결과 SHA 불일치")
            require(result["insights_markdown"] == state["final_insights"] == interviews[-1]["insights_after"], "최종 기준 내용 불일치")
            require(result["insights_sha256"] == digest(state["final_insights"]) == interviews[-1]["insights_after_sha256"], "네 번째 최종 기준 SHA 불일치")
            require(result["interviews"] == [interview_summary(iv) for iv in interviews], "최종 결과의 면접별 확정 해시·시간 불일치")
            ranking = result["ranking"]
            require(isinstance(ranking, list) and len(ranking) == 4, "순위 후보 수 오류")
            require({item["candidate_id"] for item in ranking} == set(ids), "순위 후보 중복·누락")
            require(all(type(item["rank"]) is int for item in ranking) and {item["rank"] for item in ranking} == {1, 2, 3, 4}, "순위 중복·범위 오류")
            evidence_count = 0
            for item in ranking:
                require(all(isinstance(item[k], str) and bool(item[k].strip()) for k in ("rationale", "uncertainty")), "순위 근거·불확실성 누락")
                require(isinstance(item["evidence"], list) and bool(item["evidence"]), "인용 근거 누락")
                for evidence in item["evidence"]:
                    source_record = sources.get(evidence["source_id"])
                    quote = evidence["quote"]
                    require(source_record is not None and source_record[0] == item["candidate_id"], "다른 후보·면접관·없는 출처 인용")
                    require(isinstance(quote, str) and bool(quote.strip()) and quote in source_record[1], "인용문이 해당 후보 원문과 정확히 일치하지 않는다")
                    evidence_count += 1
            report.update(result_sha256=result["result_sha256"], evidence_quotes_checked=evidence_count,
                          final_insights_sha256=result["insights_sha256"])
            checked("final_result_hash_ranks_and_exact_same_candidate_evidence")

            report["ranking_verification"] = {"status": "passed"}

        outputs = expected_outputs(state)
        require(set(outputs) == set(state["artifacts"]), "확정 artifact 목록 불일치")
        for name, text in outputs.items():
            artifact = state["artifacts"][name]
            require(artifact.get("pending") is False and "previous_sha256" not in artifact, "아직 반영되지 않은 artifact: " + name)
            expected_hash = digest(text)
            require(artifact["sha256"] == expected_hash and file_digest(inside(session, name)) == expected_hash,
                    "확정 artifact·재구성 결과·파일 SHA 불일치: " + name)
        report["artifacts_checked"] = len(outputs)
        checked("all_committed_artifact_byte_hashes_and_reconstructed_documents")
        require((session / "state.json").read_bytes() == state_bytes and notice_path.read_bytes() == notice_bytes,
                "검증 중 상태 또는 고지가 변경되었다")
        checked("state_and_notice_unchanged_during_verification")
        report["status"] = "interviews_verified_finalization_incomplete" if incomplete else "passed"
    except (Invalid, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        report["error"] = str(exc) if isinstance(exc, Invalid) else "입력 파일 또는 필드 형식 오류: " + type(exc).__name__
    return report


def atomic_report(path, report):
    fd, temporary = tempfile.mkstemp(prefix=".verification-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def self_test():
    """Exercises only newly created synthetic trees; never reads real sessions."""
    with tempfile.TemporaryDirectory(prefix="rehearsal-verifier-selftest-") as temporary:
        parent = Path(temporary)
        root, source = parent / "copy", parent / "source"
        for directory in (root, source):
            (directory / "harness").mkdir(parents=True)
            (directory / "harness/fixture.py").write_bytes(b"# synthetic baseline only\n")
        session = root / ".runtime/synthetic"
        session.mkdir(parents=True)
        notice = {"kind": "user-authorized-agent-as-human-rehearsal", "source": str(source), "copy": str(root),
            "copied_entire_tree": True, "code_modified": False, "session_path": ".runtime/synthetic",
            "source_files_sha256": {"harness/fixture.py": file_digest(source / "harness/fixture.py")}}
        (root / "REHEARSAL-NOTICE.json").write_text(json.dumps(notice), encoding="utf-8")
        state = {"schema_version": "founder-terminal-1", "session_id": "synthetic-only", "phase": "complete", "interviews": []}
        for number, cid in enumerate(("P07", "P01", "P04", "P06"), 1):
            start, limit = number * 10000.0, 1800 if number == 1 else 1200
            criterion = "# 합성 기준\n## 중요하게 보는 기준\n합성 책임감\n"
            iv = {"number": number, "candidate_id": cid, "name": "합성 인물", "status": "completed",
                "mode": "human" if number == 1 else "agent", "limit_seconds": limit, "started_at": start,
                "deadline_at": start + limit, "closed_at": start + 4, "next_role": None, "turn": None,
                "insights": criterion, "insights_after": criterion, "insights_sha256": digest(criterion),
                "insights_after_sha256": digest(criterion), "insight_action": "unchanged", "feedback": "합성 평가 근거",
                "feedback_sha256": digest("합성 평가 근거"), "insights_confirmation": {"at": start - 1, "sha256": digest(criterion)}}
            values = [("start", "interviewer", "system", "합성 인물님 면접 시작하겠습니다"),
                ("intro", "interviewer", "system", "합성 자기소개 요청"),
                ("reply:1", "candidate", "agent", "합성 후보 답변"),
                ("question:1", "interviewer", "human" if number == 1 else "agent", "합성 질문"),
                ("close", "interviewer", "system", "수고하셨습니다")]
            iv["messages"] = [{"id": f"local-message-{i + 1:04d}", "key": k, "speaker": s, "origin": o, "text": t, "at": start + i}
                              for i, (k, s, o, t) in enumerate(values)]
            state["interviews"].append(iv)
        state["final_insights"] = criterion
        state["final_result"] = {"schema_version": "founder-terminal-result-1", "session_id": state["session_id"],
            "insights_markdown": criterion, "insights_sha256": digest(criterion),
            "interviews": [interview_summary(iv) for iv in state["interviews"]],
            "ranking": [{"candidate_id": iv["candidate_id"], "rank": iv["number"], "rationale": "합성 근거", "uncertainty": "합성 한계",
                "evidence": [{"source_id": iv["candidate_id"] + ":feedback", "quote": "합성 평가"}]} for iv in state["interviews"]]}
        def write_fixture(value):
            if "final_result" in value:
                value["final_result"]["result_sha256"] = digest(canonical({k: v for k, v in value["final_result"].items() if k != "result_sha256"}))
            outputs = expected_outputs(value)
            value["artifacts"] = {name: {"sha256": digest(text), "pending": False} for name, text in outputs.items()}
            for name, text in outputs.items():
                path = session / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode("utf-8"))
            value["state_sha256"] = digest(canonical({k: v for k, v in value.items() if k != "state_sha256"}))
            (session / "state.json").write_text(canonical(value), encoding="utf-8")
        write_fixture(state)
        require(verify(root)["status"] == "passed", "합성 정상 세션 검사 실패")
        tested = ["valid_fixture"]
        partial = deepcopy(state)
        partial.update(phase="finalizing")
        partial.pop("final_result")
        write_fixture(partial)
        for name in ("ranking.json", "ranking.md"):
            (session / name).unlink()
        require(verify(root)["status"] == "failed", "기본 검사에서 순위 미완료를 허용했다")
        tested.append("incomplete_finalization_requires_opt_in")
        reviewed = verify(root, allow_incomplete_finalization=True)
        require(reviewed["status"] == "interviews_verified_finalization_incomplete"
                and reviewed["ranking_verification"]["status"] == "skipped"
                and reviewed["artifacts_checked"] == 16 and reviewed["finalization_complete"] is False,
                "명시적 부분 검사 오류")
        tested.append("explicit_interview_only_verification")
        for name in ("ranking.json", "ranking.md"):
            (session / name).write_text("합성 잔여 순위", encoding="utf-8")
            require(verify(root, allow_incomplete_finalization=True)["status"] == "failed", "잔여 순위 파일 미탐지")
            (session / name).unlink()
        tested.append("incomplete_with_ranking_files_rejected")
        invalid_partial = deepcopy(state)
        invalid_partial["phase"] = "finalizing"
        write_fixture(invalid_partial)
        require(verify(root, allow_incomplete_finalization=True)["status"] == "failed", "순위 대기 상태의 결과 미탐지")
        tested.append("incomplete_with_final_result_rejected")
        for name in ("ranking.json", "ranking.md"):
            (session / name).unlink()
        unfinished = deepcopy(partial)
        unfinished["interviews"][-1]["status"] = "feedback"
        write_fixture(unfinished)
        require(verify(root, allow_incomplete_finalization=True)["status"] == "failed", "네 번째 미확정 평가를 허용했다")
        tested.append("unconfirmed_fourth_feedback_rejected")
        wrong_phase = deepcopy(partial)
        wrong_phase["phase"] = "interviews"
        write_fixture(wrong_phase)
        require(verify(root, allow_incomplete_finalization=True)["status"] == "failed", "다른 미완료 단계에 옵션 적용")
        tested.append("other_incomplete_phase_rejected")
        write_fixture(state)
        require(verify(root, allow_incomplete_finalization=True)["status"] == "passed", "완료 상태의 전체 검증을 건너뛰었다")
        tested.append("complete_still_verifies_ranking_with_option")
        mutations = {
            "incomplete_phase": lambda s: s.update(phase="interviews"),
            "wrong_quote": lambda s: s["final_result"]["ranking"][0]["evidence"][0].update(quote="존재하지 않는 인용"),
            "other_candidate_quote": lambda s: s["final_result"]["ranking"][0]["evidence"][0].update(source_id="P01:feedback"),
            "duplicate_rank": lambda s: s["final_result"]["ranking"][0].update(rank=2),
            "late_utterance": lambda s: s["interviews"][0]["messages"][2].update(at=s["interviews"][0]["deadline_at"] + 1),
            "duplicate_id": lambda s: s["interviews"][0]["messages"][2].update(id="local-message-0001"),
            "non_monotonic_time": lambda s: s["interviews"][0]["messages"][2].update(at=s["interviews"][0]["started_at"] - 1),
            "wrong_start_text": lambda s: s["interviews"][0]["messages"][0].update(text="잘못된 합성 시작"),
            "changed_limit": lambda s: s["interviews"][0].update(limit_seconds=2000),
            "snapshot_hash": lambda s: s["interviews"][0].update(insights="변경된 합성 기준"),
        }
        for name, mutate in mutations.items():
            changed = deepcopy(state)
            mutate(changed)
            write_fixture(changed)
            require(verify(root)["status"] == "failed", "합성 오류를 놓쳤다: " + name)
            tested.append(name)
        write_fixture(state)
        (session / "ranking.md").write_text("합성 외부 변경", encoding="utf-8")
        require(verify(root)["status"] == "failed", "artifact 변경 미탐지")
        tested.append("artifact_tamper")
        write_fixture(state)
        raw = json.loads((session / "state.json").read_text())
        raw["state_sha256"] = "0" * 64
        (session / "state.json").write_text(json.dumps(raw), encoding="utf-8")
        require(verify(root)["status"] == "failed", "상태 해시 변경 미탐지")
        tested.append("state_hash_tamper")
        write_fixture(state)
        raw = json.loads((session / "state.json").read_text())
        raw["final_result"]["result_sha256"] = "0" * 64
        raw["state_sha256"] = digest(canonical({k: v for k, v in raw.items() if k != "state_sha256"}))
        (session / "state.json").write_text(canonical(raw), encoding="utf-8")
        require(verify(root)["error"] == "최종 결과 SHA 불일치", "결과 해시 변경 미탐지")
        tested.append("result_hash_tamper")
        delayed = deepcopy(state)
        iv = delayed["interviews"][0]
        iv["closed_at"] = iv["deadline_at"] + .25
        iv["messages"][-1]["at"] = iv["closed_at"]
        delayed["final_result"]["interviews"] = [interview_summary(item) for item in delayed["interviews"]]
        write_fixture(delayed)
        require(verify(root)["status"] == "failed", "종료 지연 사유 누락 미탐지")
        tested.append("undocumented_closing_delay")
        iv["protocol_note"] = "합성 실행 지연 0.25초"
        delayed["final_result"]["interviews"] = [interview_summary(item) for item in delayed["interviews"]]
        write_fixture(delayed)
        reviewed = verify(root)
        require(reviewed["status"] == "passed" and len(reviewed["warnings"]) == 1, "문서화된 종료 지연 처리 오류")
        tested.append("documented_closing_delay")
        write_fixture(state)
        (root / "harness/fixture.py").write_bytes(b"# changed synthetic copy\n")
        require(verify(root)["status"] == "failed", "복사본 변경 미탐지")
        tested.append("copy_baseline_tamper")
        (root / "harness/fixture.py").write_bytes((source / "harness/fixture.py").read_bytes())
        (source / "harness/fixture.py").write_bytes(b"# changed synthetic baseline\n")
        require(verify(root)["status"] == "failed", "원본 변경 미탐지")
        tested.append("source_baseline_tamper")
        print(json.dumps({"kind": "synthetic-self-test-only", "passed": len(tested), "cases": tested,
                          "real_session_read": False, "model_calls": 0}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="새 임시 폴더의 합성 자료만 검사")
    parser.add_argument("--allow-incomplete-finalization", action="store_true",
                        help="네 명 평가 완료·순위 미생성일 때 면접 기록만 검증; 순위는 skipped, 종료 코드 2")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    destination = Path(__file__).resolve().parent
    report = verify(destination.parent, allow_incomplete_finalization=args.allow_incomplete_finalization)
    atomic_report(destination / "verification.json", report)
    print(json.dumps({"status": report["status"], "checks_passed": len(report["checks"]),
                      "report": str(destination / "verification.json"), "error": report.get("error")}, ensure_ascii=False))
    return {"passed": 0, "interviews_verified_finalization_incomplete": 2}.get(report["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
