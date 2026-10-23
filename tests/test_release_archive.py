"""Exercise the actual public-source ZIP builder, including negative cases."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location('local_scrape_release_builder', Path(__file__).resolve().parents[1] / 'packaging/make_release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


@pytest.mark.parametrize('name', [
    '.env', 'config.json', 'data/api-token', 'data/state.sqlite3', 'logs/api.log',
    '.venv/Scripts/python.exe', 'browsers/chrome.exe', 'runtime/ollama.exe',
    'evidence/private.json', '.git/config', '.cache/private.py',
    'tests/__pycache__/test_example.pyc', '../README.md', '/README.md',
    'skills/local-scrape/../../.env', 'docs/private.txt',
])
def test_private_or_unsafe_paths_are_not_public(name):
    assert not release.is_public_path(name)


@pytest.mark.parametrize('name', [
    '.env.example', 'README.md', 'local_scrape/cli.py', 'tests/test_cli.py',
    'scripts/bootstrap_runtime.py', 'packaging/make_release.py',
    'skills/local-scrape/SKILL.md', 'skills/local-scrape/references/installation.md',
])
def test_public_source_paths_are_allowed(name):
    assert release.is_public_path(name)


def fixture_project(root):
    root.mkdir()
    for relative in release.REQUIRED_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture source\n', encoding='utf-8')
    (root / 'pyproject.toml').write_text('[project]\nversion = "0.1.0"\n', encoding='utf-8')
    (root / '.env.example').write_text('OLLAMA_API_KEY=\n', encoding='utf-8')
    for relative in ('.env', 'config.json', 'data/api-token', '.cache/hidden.py', 'logs/api.log'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private fixture - must never ship\n', encoding='utf-8')


def test_release_contains_exact_sources_and_bound_manifest(tmp_path):
    root = tmp_path / 'Application With Spaces'
    fixture_project(root)
    result = release.build(root, tmp_path / 'output')
    archive_path = Path(result['zip'])
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == result['sha256']
    expected = {path.relative_to(root).as_posix() for path in release.public_files(root)}
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        prefix = 'local-scrape-0.1.0/'
        assert len(names) == len({name.casefold() for name in names})
        actual = {name.removeprefix(prefix) for name in names}
        assert actual == expected | {'RELEASE-MANIFEST.json'}
        manifest = json.loads(archive.read(prefix + 'RELEASE-MANIFEST.json'))
        assert {row['path'] for row in manifest['files']} == expected
        for row in manifest['files']:
            payload = archive.read(prefix + row['path'])
            assert payload == (root / row['path']).read_bytes()
            assert row['bytes'] == len(payload)
            assert row['sha256'] == hashlib.sha256(payload).hexdigest()
    assert result['zip_members'] == result['payload_files'] + 1
    assert archive_path.name in Path(result['checksums']).read_text()


def test_missing_bundled_skill_fails_closed(tmp_path):
    fixture_project(tmp_path / 'app')
    (tmp_path / 'app/skills/local-scrape/SKILL.md').unlink()
    with pytest.raises(ValueError, match='Required public files are missing'):
        release.build(tmp_path / 'app', tmp_path / 'output')


def test_existing_archive_is_not_overwritten(tmp_path):
    root = tmp_path / 'app'
    fixture_project(root)
    result = release.build(root, tmp_path / 'output')
    before = Path(result['zip']).read_bytes()
    with pytest.raises(FileExistsError):
        release.build(root, tmp_path / 'output')
    assert Path(result['zip']).read_bytes() == before
