"""Background local checks, joined before any interview session is created."""
from pathlib import Path
import subprocess
import sys
import threading

from .auth import claude_environment


class StartupChecks:
    def __init__(self, root=None, runner=None):
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
        self.runner = runner or subprocess.run
        self.log_path = self.root / ".bootstrap" / "background-checks.log"
        self.error = None
        # Do not pass inherited API keys or retired operator credentials to child checks.
        self.env = {**claude_environment(), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        self.thread = threading.Thread(target=self._run, name="founder-startup-checks", daemon=True)
        self.thread.start()
        print("로컬 테스트와 자료 점검을 진행합니다. 완료되면 면접 기준을 확인합니다.", flush=True)

    def _run(self):
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("w", encoding="utf-8") as log:
                failures = []
                for label, arguments in (("pytest", ["pytest", "-q"]),
                                         ("doctor", ["harness", "doctor"])):
                    log.write(f"[{label}] 시작\n")
                    log.flush()
                    result = self.runner([sys.executable, "-m", *arguments], cwd=self.root,
                                         env=self.env, stdout=log, stderr=subprocess.STDOUT,
                                         timeout=180)
                    log.write(f"[{label}] exit={result.returncode}\n")
                    if result.returncode:
                        failures.append(label)
                if failures:
                    raise ValueError(" / ".join(failures) + " 점검 실패")
                log.write("로컬 점검 통과. 실제 Claude 응답·Mac/Windows 입력 검증은 별도입니다.\n")
        except Exception as exc:
            self.error = exc
            try:
                with self.log_path.open("a", encoding="utf-8") as log:
                    log.write(f"점검 중단: {type(exc).__name__}\n")
            except OSError:
                pass

    def wait(self):
        if self.thread.is_alive():
            print("백그라운드 로컬 점검 결과를 확인합니다.", flush=True)
        self.thread.join()
        if self.error:
            raise ValueError(f"로컬 점검을 통과하지 못했습니다. {self.log_path}를 확인한 뒤 다시 시작하세요.") from self.error
        print("로컬 테스트·자료 점검 통과. 실제 Claude 응답과 Mac/Windows 입력은 아직 확인하지 않았습니다.", flush=True)
