"""Standard source/wheel contracts; synthetic checks, no Claude invocation."""
from email.parser import Parser
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("harness", "materials", "candidate_runtime", "prompts", "templates")
RESOURCE_PATTERNS = {
    "harness": ("candidate_pools.json",),
    "materials": ("company/*.md", "candidates/*/*.json", "candidates/*/*.txt", "candidates/*/*.pdf", "candidates/*/*.md"),
    "candidate_runtime": ("manifest.json", "personas/*.json"),
    "prompts": ("*.md",), "templates": ("*.md",),
}


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=120, **kwargs)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    return result.stdout


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    directory = tmp_path_factory.mktemp("wheel-contract")
    source = directory / "source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copyfile(ROOT / name, source / name)
    for package in PACKAGES:
        shutil.copytree(ROOT / package, source / package,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    # These synthetic unlisted local files must not become wheel resources.
    (source / ".env").write_text("SYNTHETIC_LOCAL_VALUE=not-a-credential\n")
    (source / "harness/local-credentials.json").write_text('{"synthetic":true}')
    command([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index",
             "--wheel-dir", str(directory / "dist"), str(source)], cwd=directory)
    wheels = list((directory / "dist").glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_source_uses_standard_install_and_keeps_test_tools_optional():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["scripts"]["founder-light"] == "harness.__main__:main"
    assert not any("pytest" in item or "setuptools" in item or "wheel" in item
                   for item in project["project"]["dependencies"])
    extras = project["project"]["optional-dependencies"]["test"]
    assert all(any(item.startswith(name) for item in extras) for name in ("pytest", "setuptools", "wheel"))
    assert project["tool"]["setuptools"]["include-package-data"] is False
    for obsolete in ("scripts/package.py", "bootstrap.py", "setup.md", "start-windows.ps1", "실행.command", "실행.bat"):
        assert not (ROOT / obsolete).exists(), obsolete


def test_wheel_contains_every_runtime_resource_without_local_files(wheel):
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for package in PACKAGES:
            assert package + "/__init__.py" in names
        for package, patterns in RESOURCE_PATTERNS.items():
            for pattern in patterns:
                paths = list((ROOT / package).glob(pattern))
                assert paths, (package, pattern)
                for path in paths:
                    name = path.relative_to(ROOT).as_posix()
                    assert name in names, name
                    assert archive.read(name) == path.read_bytes(), name
        assert "prompts/candidate.md" in names
        assert len([name for name in names if name.startswith("candidate_runtime/personas/")]) == 8
        assert len([name for name in names if name.startswith("materials/candidates/") and name.endswith("/application.json")]) == 8
        assert not any(any(part in name for part in (".runtime/", ".env", "__pycache__/", "tests/", "scripts/",
                                                    "local-credentials", "bootstrap", ".venv/")) for name in names)
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_path).decode("utf-8"))
        for requirement in metadata.get_all("Requires-Dist", []):
            if requirement.startswith(("pytest", "setuptools", "wheel")):
                assert 'extra == "test"' in requirement


def test_installed_wheel_loads_resources_away_from_checkout(wheel, tmp_path):
    target, working = tmp_path / "installed", tmp_path / "unrelated-working-directory"
    working.mkdir()
    command([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(target), str(wheel)], cwd=working)
    # -I discards cwd/PYTHONPATH. Fail on any editable/source-package fallback
    # before using resources; only ordinary installed runtime dependencies are shared.
    # It also ignores PYTHONUTF8, so explicitly use UTF-8 for Korean output on Windows.
    code = '''import importlib, importlib.metadata, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
for name in ("harness", "materials", "candidate_runtime", "prompts", "templates"):
    package = importlib.import_module(name)
    assert pathlib.Path(package.__file__).resolve().is_relative_to(root), name
from harness.candidate import validate_bundle, runtime_fingerprint
assert len(validate_bundle()) == 8
assert len(runtime_fingerprint()) == 64
dist = importlib.metadata.distribution("founder-side-light")
assert any(e.group == "console_scripts" and e.name == "founder-light" and e.value == "harness.__main__:main" for e in dist.entry_points)
from harness.__main__ import doctor
doctor()
print("ISOLATED_WHEEL_RESOURCES_OK")
'''
    output = command([sys.executable, "-I", "-X", "utf8", "-c", code, str(target)], cwd=working)
    assert "ISOLATED_WHEEL_RESOURCES_OK" in output


def test_git_archive_excludes_accidentally_tracked_state_credentials_and_caches(tmp_path):
    repository = tmp_path / "synthetic-repository"
    repository.mkdir()
    shutil.copyfile(ROOT / ".gitattributes", repository / ".gitattributes")
    public = ["README.md", "pyproject.toml", "harness/__init__.py", "harness/candidate_pools.json",
              "candidate_runtime/personas/P07.json", "prompts/candidate.md", "templates/insights.md",
              "tests/test_sample.py", ".github/workflows/tests.yml"]
    private = [".runtime/terminal-light/state.json", ".runtime/terminal-light/01-P07/transcript.md",
               "rehearsal-results/verification.json", "REHEARSAL-NOTICE.json", ".env", ".env.production",
               ".claude/credentials.json", ".codex/config.toml", "credentials.json", ".credentials.json",
               "example.pem", "example.key", "example.p12", "example.pfx", "config/secrets.local.json",
               ".venv/pyvenv.cfg", "venv/pyvenv.cfg", ".bootstrap/status.json", "harness/__pycache__/fixture.pyc",
               ".pytest_cache/cache", ".mypy_cache/cache", ".ruff_cache/cache", ".coverage", ".coverage.1",
               "htmlcov/index.html", "build/output", "dist/package.whl", "sample.egg-info/PKG-INFO",
               ".DS_Store", "._resource", "Thumbs.db", "debug.log", "fixture.swp", "fixture.swo", "transcript.pdf"]
    for name in public + private:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic archive fixture\n", encoding="utf-8")
    command(["git", "init", "--quiet", str(repository)], cwd=tmp_path)
    command(["git", "-c", "core.autocrlf=false", "add", "--all", "--force"], cwd=repository)
    tree = command(["git", "write-tree"], cwd=repository).strip()
    # No commit or worktree Git mutation: git archive accepts this synthetic tree.
    data = subprocess.run(["git", "archive", "--format=tar", tree], cwd=repository,
                          capture_output=True, check=True, timeout=30).stdout
    with tarfile.open(fileobj=BytesIO(data), mode="r:") as archive:
        members = {item.name for item in archive.getmembers() if item.isfile()}
    assert set(public) <= members
    assert not set(private) & members
