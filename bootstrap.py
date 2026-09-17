"""First-run installer. Standard library only until the dedicated venv is ready."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import urllib.request

ROOT = Path(__file__).resolve().parent


def detect_platform():
    selected = {"Darwin": "mac", "Windows": "windows"}.get(platform.system())
    if selected is None:
        raise ValueError("시작 파일은 Mac과 Windows를 지원합니다")
    return selected


def run(command, **kwargs):
    subprocess.run([str(arg) for arg in command], check=True, **kwargs)


def open_setup_guide():
    guide = ROOT / "setup.md"
    if not guide.is_file() or os.environ.get("FOUNDER_SETUP_OPENED") == "1":
        return
    try:
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", str(guide)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-t", str(guide)])
        os.environ["FOUNDER_SETUP_OPENED"] = "1"
    except OSError:
        print(f"setup.md를 직접 여세요: {guide}", flush=True)


def install_script(url, command):
    suffix = ".ps1" if url.endswith(".ps1") else ".sh"
    with tempfile.TemporaryDirectory(prefix="founder-install-") as temporary:
        path = Path(temporary) / ("install" + suffix)
        with urllib.request.urlopen(url, timeout=60) as response:
            path.write_bytes(response.read())
        run([*command, path])


def resolve_uv(selected, requested=None):
    """Use the requested uv, or install a checkout-local one for direct runs."""
    if requested:
        return str(requested)
    local_bin = ROOT / ".bootstrap" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    executable = local_bin / ("uv.exe" if selected == "windows" else "uv")
    if executable.is_file():
        return str(executable)
    os.environ["UV_INSTALL_DIR"] = str(local_bin)
    os.environ["UV_NO_MODIFY_PATH"] = "1"
    if selected == "mac":
        print("Python 실행 환경을 준비합니다.", flush=True)
        install_script("https://astral.sh/uv/install.sh", ["bash"])
    else:
        print("Python 실행 환경을 준비합니다.", flush=True)
        install_script("https://astral.sh/uv/install.ps1",
                       ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
    if not executable.is_file():
        raise ValueError("uv 설치가 완료되지 않았습니다. 같은 명령을 다시 실행하세요.")
    return str(executable)


def install_claude(selected):
    from harness.auth import claude_executable
    try:
        return claude_executable()
    except ValueError:
        print("Claude Code를 설치합니다.", flush=True)
        if selected == "mac":
            install_script("https://claude.ai/install.sh", ["bash"])
        else:
            install_script("https://claude.ai/install.ps1",
                           ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
        return claude_executable()


def install_environment(selected, uv, venv, python, installed, fingerprint, log_path):
    """Install the isolated environment without making the login prompt wait visibly."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("경량 면접 하네스 의존성 설치\n")
        log.flush()
        created = not python.is_file()
        if created:
            if selected == "windows":
                # Use the already-running, signed system interpreter. This avoids
                # Smart App Control rejecting uv's managed CPython on Windows.
                run([sys.executable, "-m", "venv", "--clear", venv], stdout=log, stderr=subprocess.STDOUT)
            else:
                run([uv, "venv", "--allow-existing", "--python", "3.12", venv],
                    stdout=log, stderr=subprocess.STDOUT)
        if created or installed != {"os": selected, "fingerprint": fingerprint}:
            print("필요한 Python 패키지를 백그라운드에서 설치합니다.", flush=True)
            if selected == "windows":
                command = [python, "-m", "pip", "install", "--disable-pip-version-check",
                           "-c", ROOT / "requirements.lock.txt", "-e", str(ROOT) + "[test]"]
            else:
                command = [uv, "pip", "install", "--python", python,
                           "-c", ROOT / "requirements.lock.txt", "-e", str(ROOT) + "[test]"]
            run(command, stdout=log, stderr=subprocess.STDOUT)
        log.write("의존성 설치 완료\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--uv", help="uv 실행 파일 (생략하면 .bootstrap에 자동 설치)")
    args = parser.parse_args(argv)
    selected = detect_platform()
    os.chdir(ROOT)
    open_setup_guide()
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if selected == "windows":
        if sys.version_info < (3, 11):
            raise ValueError("Windows에서는 서명된 Python 3.11 이상이 필요합니다. 실행.bat이 py -3.13부터 확인합니다.")
        uv = None
    else:
        uv = resolve_uv(selected, args.uv)
    # One installer per checkout. The installer itself has no Python dependencies.
    lock_path = ROOT / ".bootstrap" / "installer.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock.tell() == 0:
                lock.write(b"0"); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        install_claude(selected)
        from harness.auth import ensure_claude_login
        venv = ROOT / ".venv"
        python = venv / ("Scripts/python.exe" if selected == "windows" else "bin/python")
        fingerprint = hashlib.sha256(
            b"terminal-startup-with-tests-v2" + (ROOT / "requirements.lock.txt").read_bytes()
            + (ROOT / "pyproject.toml").read_bytes()).hexdigest()
        stamp = ROOT / ".bootstrap" / "installed.json"
        try:
            installed = json.loads(stamp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            installed = {}
        install_log = ROOT / ".bootstrap" / "install.log"
        install_error = []

        def install_worker():
            try:
                install_environment(selected, uv, venv, python, installed, fingerprint, install_log)
            except BaseException as exc:  # re-raise in the foreground after login completes
                install_error.append(exc)

        worker = threading.Thread(target=install_worker, name="founder-environment-install")
        worker.start()
        print("면접용 Claude 인증을 확인하는 동안 실행 환경을 백그라운드에서 준비합니다.", flush=True)
        try:
            ensure_claude_login(interactive=True)
        finally:
            worker.join()
        if install_error:
            raise ValueError(f"실행 환경 설치에 실패했습니다. {install_log}를 확인한 뒤 다시 시작하세요.") from install_error[0]
        stamp.write_text(json.dumps({"os": selected, "fingerprint": fingerprint}), encoding="utf-8")
    run([python, "-m", "harness", "start", "--check-startup"])


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"설정이 완료되지 않았습니다: {exc}\n문제를 해결한 뒤 같은 시작 파일을 다시 여세요.", file=sys.stderr)
        raise SystemExit(1)
