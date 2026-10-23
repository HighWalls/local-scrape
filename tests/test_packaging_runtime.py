import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('portable_bootstrap', ROOT / 'scripts/bootstrap_runtime.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_bootstrap_defaults_to_running_interpreter():
    assert bootstrap.parse_args([]).python == sys.executable


def test_bootstrap_environment_does_not_inherit_other_python_or_pip(tmp_path):
    env = bootstrap.runtime_env(tmp_path, {'PYTHONPATH': 'foreign', 'PYTHONHOME': 'foreign',
                                           'PIP_EXTRA_INDEX_URL': 'https://invalid.example',
                                           'VIRTUAL_ENV': 'foreign'})
    assert not {'PYTHONPATH', 'PYTHONHOME', 'PIP_EXTRA_INDEX_URL', 'VIRTUAL_ENV'} & env.keys()
    assert env['LOCAL_SCRAPE_ROOT'] == str(tmp_path.resolve())



def test_failed_install_step_preserves_evidence(tmp_path):
    env = bootstrap.runtime_env(tmp_path)
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        bootstrap.bounded_run([sys.executable, '-B', '-c', 'print("test failure evidence", flush=True); raise SystemExit(7)'], env, 10)
    assert 'test failure evidence' in (tmp_path / 'evidence/runtime-install.log').read_text()



def test_lock_is_checked_without_mutating_source(tmp_path):
    lock = tmp_path / 'requirements.lock.txt'
    lock.write_text('# Exact inventory\npytest==' + __import__('pytest').__version__ + '\n', encoding='utf-8')
    before = lock.read_bytes()
    bootstrap.verify_lock(tmp_path)
    assert lock.read_bytes() == before
    lock.write_text('pytest==0.0.0\n', encoding='utf-8')
    with pytest.raises(RuntimeError, match='pytest'):
        bootstrap.verify_lock(tmp_path)


def test_unpinned_lock_is_rejected(tmp_path):
    (tmp_path / 'requirements.lock.txt').write_text('httpx>=0.27\n', encoding='utf-8')
    with pytest.raises(ValueError, match='pin'):
        bootstrap.read_lock(tmp_path)


def test_cold_venv_creation_does_not_run_ensurepip(tmp_path):
    root = tmp_path / 'Fresh Install With Spaces'
    root.mkdir()
    env = bootstrap.runtime_env(root)
    python = bootstrap.create_venv(sys._base_executable, root, env)
    result = subprocess.run([str(python), '-B', '-c', 'import importlib.util; print(importlib.util.find_spec("pip") is None)'],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'True'
    assert not (root / '.venv/Scripts/pip.exe').exists()


def test_installer_repairs_interrupted_package_tree(tmp_path, monkeypatch):
    (tmp_path / 'requirements.lock.txt').write_text('pytest==9.1.1\npip==25.1.1\n', encoding='utf-8')
    python = tmp_path / '.venv/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(bootstrap, 'ROOT', tmp_path)
    monkeypatch.setattr(bootstrap, 'pip_bootstrap_wheel', lambda root: root / '.cache/pinned-pip.whl')
    commands = []

    class StopProbe(Exception):
        pass

    def record(command, env, timeout):
        commands.append(command)
        raise StopProbe

    monkeypatch.setattr(bootstrap, 'bounded_run', record)
    with pytest.raises(StopProbe):
        bootstrap.main(['--install', '--no-browser-smoke'])
    assert '--force-reinstall' in commands[0]
    assert commands[0][-2:] == ['-r', str(tmp_path / 'requirements.lock.txt')]



@pytest.mark.parametrize('version, expected', [((3, 12), False), ((3, 13), True), ((3, 14), True), ((3, 15), False)])
def test_supported_python_versions(version, expected):
    assert bootstrap.supported_python(version) is expected


@pytest.fixture
def selector_probe(tmp_path):
    root = tmp_path / 'Install Folder With Spaces'
    (root / 'scripts').mkdir(parents=True)
    for name in ('setup.cmd', 'install-runtime.cmd'):
        source = ROOT / name
        if source.exists():
            (root / name).write_bytes(source.read_bytes())
    (root / 'scripts/install-runtime.cmd').write_bytes((ROOT / 'scripts/install-runtime.cmd').read_bytes())
    (root / 'scripts/bootstrap_runtime.py').write_text(
        'import json,sys\nfrom pathlib import Path\n'
        'Path(__file__).with_name("selector.json").write_text(json.dumps({"python": sys.executable, "version": list(sys.version_info[:2]), "args": sys.argv[1:]}))\n'
        'raise SystemExit(23)\n', encoding='utf-8')
    return root


def run_setup(root, env):
    command = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c ""{root / "setup.cmd"}" --no-browser-smoke"'
    return subprocess.run(command,
                          cwd=root.parent, env=env, capture_output=True, text=True, errors='replace', timeout=30)


@pytest.mark.skipif(os.name != 'nt', reason='Windows command selector')
def test_setup_accepts_supported_python_and_spaces(selector_probe):
    env = dict(os.environ, LOCAL_SCRAPE_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE='1')
    result = run_setup(selector_probe, env)
    assert result.returncode == 23, result.stdout + result.stderr
    report = json.loads((selector_probe / 'scripts/selector.json').read_text())
    assert report['version'] in [[3, 13], [3, 14]]
    assert report['args'] == ['--install', '--no-browser-smoke']


@pytest.mark.skipif(os.name != 'nt', reason='Windows command selector')
def test_setup_missing_explicit_python_is_clear_nonzero(selector_probe):
    env = dict(os.environ, LOCAL_SCRAPE_PYTHON=str(selector_probe / 'missing-python.exe'))
    result = run_setup(selector_probe, env)
    assert result.returncode != 0
    assert 'install python' in (result.stdout + result.stderr).lower()
    assert not (selector_probe / 'scripts/selector.json').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows command selector')
def test_setup_automatic_discovery(selector_probe):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('LOCAL_SCRAPE_PYTHON', None)
    result = run_setup(selector_probe, env)
    assert result.returncode == 23, result.stdout + result.stderr
    report = json.loads((selector_probe / 'scripts/selector.json').read_text())
    assert report['version'] in [[3, 13], [3, 14]]
