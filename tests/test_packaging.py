from pathlib import Path
import shutil
import unicodedata
import zipfile

from scripts import package


def test_zip_has_private_role_resources_but_no_legacy_or_local_state(tmp_path):
    target = package.build(output=tmp_path / 'terminal.zip')
    with zipfile.ZipFile(target) as archive:
        names=archive.namelist()
        assert 'founder-side-light/candidate_runtime/personas/P07.json' in names
        assert 'founder-side-light/prompts/candidate.md' in names
        assert 'founder-side-light/docs/candidate-quality-verification.md' in names
        verification = archive.read('founder-side-light/docs/verification.md').decode('utf-8')
        assert '(candidate-quality-verification.md)' in verification
        assert 'founder-side-light/실행.bat' in names
        for name in names:
            assert not any(part in name for part in ('.runtime/', '.bootstrap/', '.venv/', '__pycache__/',
                                                     'archive/', 'operator.py', 'computer_use.py', 'config/local.json'))


def test_unicode_decomposed_launcher_is_found_and_normalized(tmp_path, monkeypatch):
    source = package.ROOT
    for item in source.iterdir():
        if item.is_file() and unicodedata.normalize('NFC',item.name) in {
            '실행.command','실행.bat','start-windows.ps1','bootstrap.py','setup.md','pyproject.toml',
            'requirements.lock.txt','README.md','CLAUDE.md','AGENTS.md'}:
            shutil.copyfile(item,tmp_path/unicodedata.normalize('NFD', item.name))
    for folder in ('harness','materials','candidate_runtime','prompts','templates','docs','tests','scripts'):
        shutil.copytree(source/folder, tmp_path/folder, ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.setattr(package,'ROOT',tmp_path)
    with zipfile.ZipFile(package.build(windows=True)) as archive:
        assert 'founder-side-light/실행.bat' in archive.namelist()
        assert not any('실행.command' in x for x in archive.namelist())
