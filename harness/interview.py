"""One writer owns time and turns. Workers return one utterance, never write records."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import re
import threading
import time

from .candidate import candidate_context, dialogue_view
from .models import CandidateTurn, InterviewTurn
from .storage import canonical, sha

LAST_QUESTION = "면접을 마무리하기 전에 궁금한 점이나 마지막으로 하고 싶은 말씀이 있으신가요?"
FINAL_STATEMENT = "마지막으로 전하고 싶은 말씀을 부탁드립니다."
CLOSING = "수고하셨습니다"
WRAP_SECONDS = 120


class DeadlineExpired(ValueError):
    def __init__(self):
        super().__init__("면접 시간이 끝났습니다")


def start_text(iv):
    return f"{iv['name']}님 면접 시작하겠습니다"


def intro_text(iv):
    duration = "최대 30분" if iv["mode"] == "human" else "최대 20분"
    return (f"기술면접에 이어 함께 일하는 방식과 경험을 이야기 나누겠습니다. {duration} 정도 진행하고, "
            "마지막에는 질문이나 하고 싶은 말씀을 듣겠습니다. 먼저 간단한 자기소개를 부탁드립니다.")


class Interview:
    def __init__(self, session, provider, now=time.time, monotonic=time.monotonic, executor=None):
        self.session, self.iv, self.provider = session, session.current(), provider
        if not self.iv or self.iv["status"] not in {"ready", "active"}:
            raise ValueError("다음 면접 전에 현재 후보 평가를 확정하세요")
        session.validate_runtime()
        self.now, self.monotonic = now, monotonic
        self.wall_anchor, self.mono_anchor = now(), monotonic()
        self.last_wall = max(self.wall_anchor, self.iv.get("last_seen_at", self.wall_anchor))
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="interview-model")
        self.future = self.cancel_event = None
        self.last_error = None
        turn = self.iv.get("turn")
        if turn and turn["status"] == "running":
            turn["status"] = "interrupted"
            self.last_error = "이전 생성이 중단됐습니다. /retry로 현재 차례를 다시 요청하세요."
            session.save()

    def _time(self):
        return max(self.now(), self.wall_anchor + max(0, self.monotonic() - self.mono_anchor), self.last_wall)

    def _check_clock(self):
        expected = self.wall_anchor + max(0, self.monotonic() - self.mono_anchor)
        if self.now() < max(expected, self.last_wall) - 5:
            self._cancel()
            raise ValueError("시계가 뒤로 변경됐습니다. 시스템 시각을 확인한 뒤 같은 세션으로 재개하세요")
        self.last_wall = self._time()
        # Persist an idle high-water mark as well as message times. A restart
        # must not turn a clock rollback during a stalled generation into time.
        if self.iv["status"] == "active" and self.last_wall - self.iv.get("last_seen_at", 0) >= 1:
            self.iv["last_seen_at"] = self.last_wall
            self.session.save()

    def _before_deadline(self):
        if self._time() >= self.iv.get("deadline_at", float("inf")):
            raise DeadlineExpired()

    @contextmanager
    def _speech_commit(self):
        snapshot = deepcopy(self.session.state)
        try:
            yield
            # Recheck after serialization/fsync, immediately before the atomic
            # state replacement. An expired utterance never becomes official.
            self.session.save(before_commit=self._before_deadline)
        except DeadlineExpired:
            self.session.state.clear()
            self.session.state.update(snapshot)
            self.iv = self.session.current()
            raise

    def remaining(self):
        return max(0, self.iv.get("deadline_at", self._time() + self.iv["limit_seconds"]) - self._time())

    def stage(self):
        if self.iv.get("wrap_at") is not None or self.iv.get("wrap_requested"):
            return "wrap_up"
        return "opening" if self._time() < self.iv["started_at"] + 120 else "core"

    def waiting_label(self):
        if self.iv["status"] == "feedback":
            return "사용자 평가 대기"
        turn = self.iv.get("turn")
        if turn and turn["status"] in {"failed", "interrupted"}:
            return "생성 중단 · /retry 가능"
        if self.future is not None:
            return ("지원자" if turn["role"] == "candidate" else "면접관") + " 발화 생성 중…"
        return "면접관 입력 대기" if self.iv.get("next_role") == "human" else "면접 진행 중"

    def _append(self, key, speaker, text, origin="system"):
        if key not in {"start", "close"}:
            self._before_deadline()
        existing = next((m for m in self.iv["messages"] if m["key"] == key), None)
        if existing:
            if existing["text"] != text or existing["speaker"] != speaker:
                raise ValueError("같은 발화 키의 내용이 달라졌습니다")
            return existing
        message = {"id": f"local-message-{len(self.iv['messages']) + 1:04d}", "key": key,
                   "speaker": speaker, "origin": origin, "text": text, "at": self._time()}
        self.iv["messages"].append(message)
        self.iv["last_seen_at"] = message["at"]
        return message

    def _start(self):
        insights = self.session.insights()
        confirmation = self.iv.get("insights_confirmation", {})
        if confirmation.get("sha256") != sha(insights):
            raise ValueError("현재 insights.md를 확인하고 '시작'을 입력하세요")
        at = self._time()
        self.iv.update(status="active", started_at=at, deadline_at=at + self.iv["limit_seconds"],
                       insights=insights, insights_sha256=sha(insights), next_role="candidate",
                       closing_stage="interview")
        try:
            with self._speech_commit():
                self._append("start", "interviewer", start_text(self.iv))
                self._append("intro", "interviewer", intro_text(self.iv))
        except DeadlineExpired:
            # A suspended start still keeps its original deadline. Record the
            # protocol boundary, then step() closes without requesting a model.
            self._append("start", "interviewer", start_text(self.iv))
            raise

    def _cancel(self):
        if self.cancel_event:
            self.cancel_event.set()
        if self.future is not None:
            self.provider.cancel()
            self.future.cancel()
        self.future = self.cancel_event = None

    def shutdown(self):
        self._cancel()
        # An uncommitted running request becomes interrupted on next load.
        self.executor.shutdown(wait=False, cancel_futures=True)

    def context(self):
        return deepcopy({"company": self.session.state["company"],
            "candidate": {"id": self.iv["candidate_id"], "name": self.iv["name"], **self.iv["documents"]},
            "insights": self.iv["insights"], "messages": dialogue_view(self.iv),
            "phase": self.stage(), "closing_stage": self.iv["closing_stage"], "remaining_seconds": self.remaining()})

    def _wrap(self):
        iv = self.iv
        if "wrap_at" in iv:
            return
        turn = iv.get("turn")
        if self.future is not None and turn["role"] == "candidate":
            return  # Keep this candidate turn's stage until it completes.
        if self.future is not None:
            self._cancel()
            turn["status"] = "cancelled"
        with self._speech_commit():
            iv.update(wrap_at=self._time(), closing_stage="candidate_questions", next_role="candidate", turn=None)
            self._append("wrap", "interviewer", LAST_QUESTION)

    def _close(self):
        self._cancel()
        if self.iv.get("turn") and self.iv["turn"]["status"] == "running":
            self.iv["turn"]["status"] = "cancelled"
        message = self._append("close", "interviewer", CLOSING)
        self.iv.update(status="feedback", closed_at=message["at"], next_role=None)
        delay = max(0, message["at"] - self.iv["deadline_at"])
        if delay > 0:
            self.iv["protocol_note"] = f"중단 또는 실행 지연으로 마감보다 {delay:.1f}초 늦게 종료 기록. 마감 이후 발화 제외."
        self.session.save()
        return "feedback"

    def _consume(self):
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        event, self.cancel_event = self.cancel_event, None
        iv, turn = self.iv, self.iv["turn"]
        valid = (not event.is_set() and turn["status"] == "running"
                 and turn["reply_id"] == iv["messages"][-1]["id"]
                 and turn["stage"] == iv["closing_stage"] and turn["role"] == iv["next_role"])
        if not valid:
            turn["status"] = "cancelled"
            self.session.save()
            return
        try:
            schema = CandidateTurn if turn["role"] == "candidate" else InterviewTurn
            text = schema.model_validate(future.result()).text
            if CLOSING in text or "님 면접 시작하겠습니다" in text or re.search(r"(?m)^(면접관|지원자)\s*:", text):
                raise ValueError("발화 형식 위반")
        except Exception:
            turn["status"] = "failed"
            self.last_error = "Claude 발화를 완료하지 못했습니다. 타이머는 계속됩니다. /retry로 다시 요청할 수 있습니다."
            self.session.save()
            return
        # Recheck time after validation, before making speech official.
        if self.remaining() <= 0:
            turn["status"] = "cancelled"
            return
        with self._speech_commit():
            self._append("reply:" + turn["turn_id"], turn["role"], text, "agent")
            turn["status"] = "completed"
            if turn["role"] == "candidate":
                if iv["closing_stage"] == "candidate_questions":
                    iv["closing_stage"] = "candidate_questions_active"
                elif iv["closing_stage"] == "final_statement":
                    iv["closing_stage"] = "ready_to_close"
                iv["next_role"] = "human" if iv["mode"] == "human" else "interviewer"
            else:
                if iv["closing_stage"] == "candidate_questions_active":
                    self._append("final-invitation", "interviewer", FINAL_STATEMENT)
                    iv["closing_stage"] = "final_statement"
                iv["next_role"] = "candidate"

    def _schedule(self):
        iv = self.iv
        if self.future is not None or iv["next_role"] not in {"candidate", "interviewer"} or self.remaining() <= 0:
            return
        old = iv.get("turn")
        if old and old["status"] in {"failed", "interrupted", "running"}:
            return
        role, reply = iv["next_role"], iv["messages"][-1]["id"]
        key = f"{iv['candidate_id']}:{role}:{reply}:{iv['closing_stage']}"
        context = candidate_context(self.session, iv) if role == "candidate" else self.context()
        attempt = old["attempt"] + 1 if old and old["turn_id"] == key else 1
        iv["turn"] = {"turn_id": key, "reply_id": reply, "stage": iv["closing_stage"], "role": role,
                      "attempt": attempt, "status": "running", "input_sha256": sha(canonical(context)),
                      "requested_at": self._time(), "deadline_at": iv["deadline_at"]}
        self.session.save()
        self.cancel_event = threading.Event()
        self.future = self.executor.submit(self.provider.generate, role, context,
            CandidateTurn if role == "candidate" else InterviewTurn,
            timeout=min(90, self.remaining()), cancel_event=self.cancel_event)

    def step(self):
        try:
            return self._step()
        except DeadlineExpired:
            return self._close()

    def _step(self):
        self.session.check_integrity()
        if self.iv["status"] == "feedback":
            return "feedback"
        self._check_clock()
        if "started_at" not in self.iv:
            self._start()
        if self.remaining() <= 0 or self.iv.get("end_requested"):
            return self._close()
        if self.remaining() <= WRAP_SECONDS:
            self.iv["wrap_requested"] = True
        if self.iv.get("wrap_requested"):
            self._wrap()
        self._consume()
        if self.remaining() <= 0:
            return self._close()
        if self.iv.get("wrap_requested"):
            self._wrap()
        if self.iv["closing_stage"] == "ready_to_close" and self.iv["mode"] == "agent":
            return self._close()
        self._schedule()
        return "active"

    def human_question(self, text):
        self.session.check_integrity()
        self._check_clock()
        iv = self.iv
        if iv["mode"] != "human" or iv["status"] != "active":
            raise ValueError("진행 중인 사람 면접에서만 입력할 수 있습니다")
        if self.remaining() <= 0:
            raise ValueError("면접 시간이 끝났습니다")
        if iv.get("next_role") != "human" or self.future is not None:
            raise ValueError("지원자 응답을 기다리세요. 아직 면접관 차례가 아닙니다")
        if iv["closing_stage"] == "ready_to_close":
            raise ValueError("마지막 말을 들었습니다. /end로 종료하세요")
        text = InterviewTurn(text=text).text
        if CLOSING in text or "면접 시작하겠습니다" in text:
            raise ValueError("시작·종료는 하네스가 처리합니다. /last 또는 /end를 사용하세요")
        with self._speech_commit():
            self._append(f"human:{len(iv['messages'])}", "interviewer", text, "human")
            if iv["closing_stage"] == "candidate_questions_active":
                self._append("final-invitation", "interviewer", FINAL_STATEMENT)
                iv["closing_stage"] = "final_statement"
            iv["next_role"] = "candidate"

    def request_last(self):
        if self.iv["mode"] != "human" or self.iv["status"] != "active":
            raise ValueError("/last는 진행 중인 사람 면접에서만 사용할 수 있습니다")
        self.iv["wrap_requested"] = True
        self.session.save()

    def request_end(self):
        iv = self.iv
        if iv["mode"] != "human" or iv["status"] != "active" or "wrap_at" not in iv:
            raise ValueError("사람 면접에서 먼저 /last로 마지막 말을 요청하세요")
        wrap_index = next(i for i, m in enumerate(iv["messages"]) if m["key"] == "wrap")
        if not any(m["speaker"] == "candidate" for m in iv["messages"][wrap_index + 1:]):
            raise ValueError("마무리 질문에 대한 후보의 응답을 기다리세요")
        iv["end_requested"] = True
        self.session.save()

    def retry_generation(self):
        turn = self.iv.get("turn")
        if not turn or turn["status"] not in {"failed", "interrupted"}:
            raise ValueError("재시도할 실패한 차례가 없습니다")
        turn["status"] = "retry"
        self.last_error = None
        self.session.save()
