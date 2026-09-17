"""Atomic local state, immutable snapshots, and single-writer locks."""
from contextlib import contextmanager
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import tempfile
import uuid



def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write(path, text, *, before_replace=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def file_lock(path, *, blocking=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("다른 하네스가 실행 중입니다") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except OSError as exc:
                raise ValueError("다른 하네스가 실행 중입니다") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def content_sections(markdown):
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    sections = {}
    name = None
    for line in text.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            sections[name] = []
        elif name is not None:
            sections[name].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def validate_markdown(markdown, required):
    sections = content_sections(markdown)
    for name in required:
        value = sections.get(name, "")
        if not value or value in {"-", "TODO", "TBD", "작성하세요", "..."}:
            raise ValueError(f"'{name}' 항목을 사용자가 작성해야 합니다")
    return markdown


_RESOURCE_PACKAGES = {
    "candidate_runtime": "harness.resources.candidate_runtime",
    "materials": "harness.resources.materials",
    "prompts": "harness.resources.prompts",
    "templates": "harness.resources.templates",
}


def resource(package, name):
    """Read a bundled resource without exposing its storage layout to callers."""
    package_name = _RESOURCE_PACKAGES.get(package, package)
    return files(package_name).joinpath(name).read_text(encoding="utf-8")


SCHEMA = "founder-terminal-1"


class Session:
    def __init__(self, directory, *, repair=False):
        self.directory = Path(directory)
        self.state = json.loads((self.directory / "state.json").read_text(encoding="utf-8"))
        if self.state.get("schema_version") != SCHEMA:
            raise ValueError("기존 카카오 세션은 보존합니다. 새 터미널 세션 경로를 사용하세요")
        self._verify_state(self.state)
        self._revision = self.state["revision"]
        expected = self.outputs()
        if set(expected) != set(self.state["artifacts"]):
            raise ValueError("확정 기록 목록 손상")
        if any(sha(text) != self.state["artifacts"][name]["sha256"] for name, text in expected.items()):
            raise ValueError("확정 기록 해시 손상")
        self._render(repair=repair)

    @staticmethod
    def _verify_state(state):
        if state.get("state_sha256") != sha(canonical({k: v for k, v in state.items() if k != "state_sha256"})):
            raise ValueError("상태 파일 변경·손상. JSON을 직접 수정하지 마세요")

    @classmethod
    def create(cls, directory, model="sonnet", seed=None):
        from .allocation import allocate
        from .candidate import runtime_fingerprint
        directory = Path(directory)
        if (directory / "state.json").exists():
            return cls(directory)
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("새 세션은 비어 있는 폴더에 만드세요")
        if not model.strip():
            raise ValueError("Claude 모델 이름이 필요합니다")
        candidate_ids, assignment = allocate(seed=seed)
        interviews = []
        for number, cid in enumerate(candidate_ids, 1):
            application = json.loads(resource("materials", f"candidates/{cid}/application.json"))
            interviews.append({"number": number, "candidate_id": cid, "name": application["name"],
                "mode": "human" if number == 1 else "agent", "status": "ready",
                "limit_seconds": 1800 if number == 1 else 1200, "messages": [], "turn": None,
                "documents": {"resume": resource("materials", f"candidates/{cid}/resume.txt"),
                              "motivation": resource("materials", f"candidates/{cid}/support_motivation.md")}})
        state = {"schema_version": SCHEMA, "session_id": str(uuid.uuid4()), "model": model,
            "phase": "interviews", "interviews": interviews, "assignment": assignment,
            "runtime_sha256": runtime_fingerprint(), "revision": 0, "artifacts": {},
            "company": {n: resource("materials", f"company/{n}.md") for n in ("company_intro", "job_posting", "culture_public")}}
        directory.mkdir(parents=True, mode=0o700)
        atomic_write(directory / "insights.md", resource("templates", "insights.md"))
        state["state_sha256"] = sha(canonical(state))
        atomic_write(directory / "state.json", canonical(state))
        return cls(directory)

    def validate_runtime(self):
        from .candidate import runtime_fingerprint
        if runtime_fingerprint() != self.state["runtime_sha256"]:
            raise ValueError("자료·프롬프트 버전이 변경됐습니다. 기존 버전을 복원하거나 새 세션을 시작하세요")

    def _persist(self, before_commit=None):
        self.state["revision"] += 1
        self.state["state_sha256"] = sha(canonical({k: v for k, v in self.state.items() if k != "state_sha256"}))
        kwargs = {"before_replace": before_commit} if before_commit is not None else {}
        atomic_write(self.directory / "state.json", canonical(self.state), **kwargs)
        self._revision = self.state["revision"]

    def check_integrity(self):
        disk = json.loads((self.directory / "state.json").read_text(encoding="utf-8"))
        self._verify_state(disk)
        if disk["revision"] != self._revision:
            raise ValueError("다른 실행에서 상태가 변경됐습니다. 다시 여세요")
        self._check_artifacts()

    def _check_artifacts(self, repair=False):
        for name, info in self.state["artifacts"].items():
            path = self.directory / name
            if not path.resolve().is_relative_to(self.directory.resolve()):
                raise ValueError("기록 경로 손상")
            actual = sha(path.read_text(encoding="utf-8")) if path.exists() else None
            permitted = {info["sha256"]}
            if info.get("pending"):
                permitted.add(info.get("previous_sha256"))
            if actual not in permitted and not repair:
                raise ValueError(f"기록 변경·손상: {name}. restore-records 명령으로 명시적으로 복구하세요")

    def save(self, *, before_commit=None):
        self.check_integrity()
        outputs = self.outputs()
        for name, content in outputs.items():
            old = self.state["artifacts"].get(name)
            digest = sha(content)
            if old is None or old["sha256"] != digest:
                self.state["artifacts"][name] = {"sha256": digest, "pending": True,
                    "previous_sha256": old["sha256"] if old else None}
        self._persist(before_commit)  # Conversation and consumed turn are committed together.
        self._render()

    def _render(self, repair=False):
        self._check_artifacts(repair=repair)
        changed = False
        for name, content in self.outputs().items():
            info = self.state["artifacts"][name]
            path = self.directory / name
            if not path.exists() or sha(path.read_text(encoding="utf-8")) != info["sha256"]:
                atomic_write(path, content)
            if info.get("pending"):
                info.pop("previous_sha256", None)
                info["pending"] = False
                changed = True
        if changed:
            self._persist()
        # Editable feedback is created after closing; never overwrites user input.
        for iv in self.state["interviews"]:
            path = self.interview_dir(iv) / "feedback.md"
            if iv["status"] == "feedback" and not path.exists():
                atomic_write(path, resource("templates", "feedback.md").format(**iv))

    def outputs(self):
        result = {}
        for iv in self.state["interviews"]:
            prefix = self.interview_dir(iv).name + "/"
            if "started_at" in iv:
                lines = [f"# {iv['name']} ({iv['candidate_id']}) 면접 원문", ""]
                for m in iv["messages"]:
                    label = f"지원자({iv['name']})" if m["speaker"] == "candidate" else "면접관"
                    lines.extend([f"## {m['id']} · {label} · {m['at']}", "", m["text"], ""])
                result[prefix + "transcript.md"] = "\n".join(lines)
                result[prefix + "insights.before.md"] = iv["insights"]
            if iv["status"] == "completed":
                result[prefix + "feedback.confirmed.md"] = iv["feedback"]
                result[prefix + "insights.after.md"] = iv["insights_after"]
        if self.state.get("final_result"):
            value = self.state["final_result"]
            result["ranking.json"] = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            lines = ["# 최종 후보 순위", "", "네 번째 사용자 피드백 이후의 최종 기준을 적용했습니다.", ""]
            for item in value["ranking"]:
                lines.extend([f"## {item['rank']}위 · {item['candidate_id']}", "", item["rationale"], "",
                              "확인되지 않은 점: " + item["uncertainty"], ""])
                lines.extend(f"- {e['source_id']}: {e['quote']}" for e in item["evidence"])
                lines.append("")
            result["ranking.md"] = "\n".join(lines)
        return result

    def insights(self):
        return validate_markdown((self.directory / "insights.md").read_text(encoding="utf-8"), ["중요하게 보는 기준"])

    def confirm_insights(self, expected_hash, *, at):
        iv = self.current()
        if not iv or iv["status"] != "ready":
            raise ValueError("시작 전 기준만 확인할 수 있습니다")
        if sha(self.insights()) != expected_hash:
            raise ValueError("인사이트 내용이 변경됐습니다. 다시 확인하세요")
        iv["insights_confirmation"] = {"sha256": expected_hash, "at": at}
        self.save()

    def current(self):
        return next((iv for iv in self.state["interviews"] if iv["status"] != "completed"), None)

    def interview_dir(self, iv):
        return self.directory / f"{iv['number']:02d}-{iv['candidate_id']}"

    def accept_feedback(self, insight_action):
        self.check_integrity()
        iv = self.current()
        if not iv or iv["status"] != "feedback":
            raise ValueError("종료된 현재 후보의 평가만 확정할 수 있습니다")
        feedback = (self.interview_dir(iv) / "feedback.md").read_text(encoding="utf-8")
        validate_markdown(feedback, ["종합 평가", "근거", "우려·확인할 점"])
        insights = self.insights()
        changed = sha(insights) != iv["insights_sha256"]
        if insight_action not in {"updated", "unchanged"} or changed != (insight_action == "updated"):
            raise ValueError("선택한 인사이트 변경 여부와 실제 변경이 다릅니다")
        iv.update(status="completed", feedback=feedback, feedback_sha256=sha(feedback),
            insight_action=insight_action, insights_after=insights, insights_after_sha256=sha(insights))
        if self.current() is None:
            self.state.update(phase="finalizing", final_insights=insights)
        self.save()
        return iv
