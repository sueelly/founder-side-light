from concurrent.futures import Future
import json

import pytest

from harness.channel import KakaoChannel
from harness.interview import Interview
from harness.transport import ObservedMessage
from harness.storage import Session


class Clock:
    value = 1_800_000_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeCli:
    def __init__(self, clock):
        self.clock = clock
        self.items = []
        self.sent = []
        self.fail_after_send = False
        self.hide = False

    def add(self, text, author="candidate", at=None):
        message = ObservedMessage(str(len(self.items) + 1), text, author,
                             str(self.clock() if at is None else at))
        self.items.append(message)
        return message

    def messages(self, peer, since="2m"):
        return [] if self.hide else list(self.items)

    def send(self, peer, text, **kwargs):
        self.sent.append((peer, text))
        self.add(text, "me")
        if self.fail_after_send:
            self.fail_after_send = False
            raise TimeoutError("synthetic timeout after send")
        return None


class ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, **kwargs):
        pass


class FakeProvider:
    def __init__(self):
        self.calls = []

    def generate(self, role, context, output_type, timeout=90):
        self.calls.append((role, context))
        if role == "interviewer":
            return {"text": "그때 직접 맡으신 역할은 무엇이었나요?" if context["phase"] != "wrap_up"
                    else "입사 후 첫 2주는 전임자와 인수인계를 진행합니다."}
        return {"ranking": [{
            "candidate_id": candidate["candidate_id"], "rank": rank,
            "rationale": "사용자의 책임감 기준에 연결되는 경험과 평가가 있습니다.",
            "evidence": [{"source_id": candidate["user_feedback"]["source_id"], "quote": "업무를 끝까지 챙긴다"}],
            "uncertainty": "기준과 면접 길이가 달라 비교에는 한계가 있습니다.",
        } for rank, candidate in enumerate(reversed(context["candidates"]), 1)]}


@pytest.fixture
def session(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "own_author": "me", "pairing_verified": True,
        "peers": {f"P{i:02d}": {"chat": f"room-{i}", "author": "candidate"} for i in range(1, 11)},
        "operator": {"url": "https://operator.example.test/final", "email_env": "TEST_OPERATOR_EMAIL", "code_env": "TEST_OPERATOR_CODE"},
    }))
    session = Session.create(tmp_path / "session", config, ["P01", "P02", "P03", "P04"], "test-model")
    (session.directory / "insights.md").write_text("# 내 기준\n\n## 중요하게 보는 기준\n책임감과 협업을 본다.\n", encoding="utf-8")
    return session


def runner_for(session, clock, provider=None, executor=None):
    cli = FakeCli(clock)
    channel = KakaoChannel(session, session.current(), cli=cli, now=clock)
    runner = Interview(session, channel, provider or FakeProvider(), now=clock,
                       executor=executor or ImmediateExecutor())
    return runner, cli


def write_feedback(session):
    path = session.interview_dir(session.current()) / "feedback.md"
    path.write_text("# 사용자 평가\n\n## 종합 평가\n함께 일하고 싶다.\n\n## 근거\n업무를 끝까지 챙긴다.\n\n## 우려·확인할 점\n갈등 상황은 추가 확인이 필요하다.\n", encoding="utf-8")


def finish_interview(session, clock):
    runner, cli = runner_for(session, clock)
    runner.step()
    clock.advance(10)
    cli.add("저는 동료와 협업하며 계약 변경을 끝까지 확인했습니다.")
    runner.step()
    if session.current()["mode"] == "human":
        runner.request_last()
        runner.step()
        clock.advance(1)
        cli.add("궁금한 점은 없습니다. 감사합니다.")
        runner.step()
        runner.request_end()
    else:
        clock.value = session.current()["deadline_at"] - 120
        runner.step()
        clock.advance(1)
        cli.add("인수인계는 어떻게 하나요?")
        runner.step()
        runner.step()
        clock.value = session.current()["deadline_at"]
    assert runner.step() == "feedback"
    runner.shutdown()
    return cli


@pytest.fixture
def completed(session):
    clock = Clock()
    for _ in range(4):
        finish_interview(session, clock)
        write_feedback(session)
        session.accept_feedback("unchanged")
        clock.advance(10)
    return session
