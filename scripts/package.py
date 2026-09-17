"""Build a participant ZIP from an explicit allowlist, excluding local state."""
import argparse
from pathlib import Path
import zipfile
import unicodedata

ROOT = Path(__file__).resolve().parents[1]


def build(*, windows=False, output=None):
    output = Path(output) if output else ROOT / "dist" / (
        "light-ver-windows.zip" if windows else "founder-side-light.zip"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    names = ("실행.bat", "start-windows.ps1", "bootstrap.py",
                                     "setup.md", "pyproject.toml", "requirements.lock.txt", "README.md",
                                     "CLAUDE.md", "AGENTS.md")
    if not windows:
        names = ("실행.command",) + names
    root_files = {unicodedata.normalize("NFC", p.name): p for p in ROOT.iterdir() if p.is_file()}
    files = [root_files[name] for name in names]
    for folder in ("harness", "materials", "candidate_runtime", "prompts", "templates", "tests", "scripts"):
        files.extend(path for path in (ROOT / folder).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix in {".py", ".md", ".json", ".txt", ".pdf"})
    files += [ROOT / "docs" / name for name in (
        "terminal-interview-spec.md", "terminal-interview-plan.md", "first-run.md", "verification.md",
        "candidate-quality-verification.md")]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            if not path.resolve().is_relative_to(ROOT.resolve()):
                raise ValueError("패키지 경로 밖의 파일은 포함할 수 없습니다")
            archive.write(path, "founder-side-light/" + unicodedata.normalize("NFC", path.relative_to(ROOT).as_posix()))
    print(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", action="store_true", help="Windows 시작 파일만 포함")
    parser.add_argument("--output", help="생성할 ZIP 경로")
    args = parser.parse_args()
    build(windows=args.windows, output=args.output)
