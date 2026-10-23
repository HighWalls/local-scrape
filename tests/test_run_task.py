"""Regression tests exercise real Windows ownership and the actual API process."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

from local_scrape import cli, lifecycle
from local_scrape.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def portable_app(tmp_path):
    root = tmp_path / 'Application With Spaces'
    root.mkdir()
    shutil.copytree(ROOT / 'local_scrape', root / 'local_scrape', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'scripts', root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('run.cmd', 'start.cmd', 'stop.cmd'):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, root / name)
    subprocess.run([sys._base_executable, '-B', '-m', 'venv', '--without-pip', str(root / '.venv')], check=True, timeout=60)
    # Reuse only this test interpreter's installed dependencies, without modifying it.
    site = Path(sys.prefix) / 'Lib/site-packages'
    (root / '.venv/Lib/site-packages/test-dependencies.pth').write_text(str(site) + '\n', encoding='utf-8')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    (root / 'config.json').write_text(json.dumps({'api_port': port, 'credential_env_file': None}), encoding='utf-8')
    settings = load_settings(root=root)
    yield root, settings
    state = lifecycle._load_state(settings)
    for record in state.values():
        if isinstance(record, dict) and 'pid' in record and lifecycle._owned(record):
            lifecycle._terminate_owned(record)


def task_runner():
    from scripts.run_task import main
    return main


@pytest.mark.skipif(os.name != 'nt', reason='Windows lifecycle')
@pytest.mark.parametrize('outcome', ['success', 'error', 'interrupt'])
def test_task_cleans_real_service_on_all_exits(portable_app, outcome, capsys):
    root, settings = portable_app
    seen = []

    def operation(argv):
        state = lifecycle._load_state(settings)
        seen.append(state['api'])
        assert lifecycle.status(settings)['api']['healthy']
        if outcome == 'interrupt':
            raise KeyboardInterrupt
        if outcome == 'error':
            return cli.main(['--root', str(root), 'extract', '--prompt', 'x', '--text-file', str(root / 'missing.txt')])
        return cli.main(argv)

    code = task_runner()(['history'], root=root, operation=operation)
    assert code == {'success': 0, 'error': 1, 'interrupt': 130}[outcome]
    assert len(seen) == 1
    assert not lifecycle._owned(seen[0])
    assert not lifecycle._port_open(settings['api_port'])
    assert lifecycle._load_state(settings) == {}
    assert not (root / 'data/lifecycle.lock').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows lifecycle')
def test_task_refuses_and_preserves_preexisting_owned_api(portable_app, capsys):
    root, settings = portable_app
    lifecycle.start(settings)
    before = lifecycle._load_state(settings)
    called = []
    try:
        code = task_runner()(['history'], root=root, operation=lambda args: called.append(args))
        assert code != 0
        assert called == []
        assert lifecycle._load_state(settings) == before
        assert lifecycle.status(settings)['api']['healthy']
    finally:
        lifecycle.stop(settings)


@pytest.mark.skipif(os.name != 'nt', reason='Windows lifecycle')
def test_task_preserves_foreign_listener(portable_app):
    root, settings = portable_app
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', settings['api_port']))
        listener.listen()
        code = task_runner()(['history'], root=root)
        assert code != 0
        assert listener.getsockname()[1] == settings['api_port']
        assert lifecycle._port_open(settings['api_port'])
    assert not lifecycle._load_state(settings)


@pytest.mark.skipif(os.name != 'nt', reason='Windows lifecycle')
def test_task_holds_lease_against_other_lifecycle_commands(portable_app):
    root, settings = portable_app

    def operation(argv):
        with pytest.raises(Exception) as error:
            lifecycle.stop(settings)
        assert getattr(error.value, 'code', '') == 'LIFECYCLE_BUSY'
        return cli.main(argv)

    assert task_runner()(['history'], root=root, operation=operation) == 0
    assert not lifecycle._port_open(settings['api_port'])


@pytest.mark.skipif(os.name != 'nt', reason='Windows lifecycle')
def test_spawn_retries_transient_handshake_sharing_violation(portable_app, monkeypatch):
    root, settings = portable_app
    read_text = Path.read_text
    rejected = []

    def transient_read(path, *args, **kwargs):
        if path.name.startswith('ownership-') and path.suffix == '.json' and not rejected:
            rejected.append(path)
            raise PermissionError('simulated Windows sharing violation')
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', transient_read)
    assert task_runner()(['history'], root=root) == 0
    assert len(rejected) == 1
    assert not lifecycle._port_open(settings['api_port'])


def test_child_environment_avoids_cold_bytecode_storm(tmp_path):
    settings = load_settings(root=tmp_path, overrides={'credential_env_file': None})
    env = lifecycle.process_environment(settings)
    assert env['PYTHONDONTWRITEBYTECODE'] == '1'
    for name in ('TEMP', 'TMP', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'):
        assert Path(env[name]).is_relative_to(tmp_path)


@pytest.mark.skipif(os.name != 'nt', reason='Windows wrappers')
@pytest.mark.parametrize('arguments, expected', [('history', 0), ('scrape file:///not-public', 1)])
def test_run_cmd_from_another_cwd_cleans_service(portable_app, arguments, expected):
    root, settings = portable_app
    command = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c ""{root / "run.cmd"}" {arguments}"'
    result = subprocess.run(command, cwd=root.parent, env=lifecycle.process_environment(settings),
                            capture_output=True, text=True, errors='replace', timeout=120)
    assert result.returncode == expected, result.stdout + result.stderr
    assert json.loads(result.stdout)['ok'] is (expected == 0)
    assert lifecycle._load_state(settings) == {}
    assert not lifecycle._port_open(settings['api_port'])


@pytest.mark.skipif(os.name != 'nt', reason='Windows lifecycle')
def test_runner_cleans_real_api_after_sigint(portable_app):
    root, settings = portable_app
    code = (f'import sys; sys.path.insert(0, {str(root)!r}); '
            'import signal; from scripts.run_task import main; '
            'raise SystemExit(main(["history"], operation=lambda args: signal.raise_signal(signal.SIGINT)))')
    result = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-B', '-c', code], cwd=root.parent,
                            env=lifecycle.process_environment(settings), capture_output=True, text=True,
                            errors='replace', timeout=120)
    assert result.returncode == 130, result.stdout + result.stderr
    assert json.loads(result.stdout)['error']['code'] == 'INTERRUPTED'
    assert lifecycle._load_state(settings) == {}
    assert not lifecycle._port_open(settings['api_port'])


@pytest.mark.skipif(os.name != 'nt', reason='Windows wrappers')
def test_persistent_wrappers_ignore_another_installation_env(portable_app):
    root, settings = portable_app
    env = lifecycle.process_environment(settings)
    env['LOCAL_SCRAPE_ROOT'] = str(root.parent / 'Not This Installation')

    def run(name):
        command = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c ""{root / name}""'
        return subprocess.run(command, cwd=root.parent, env=env, capture_output=True,
                              text=True, errors='replace', timeout=120)

    try:
        started = run('start.cmd')
        assert started.returncode == 0, started.stdout + started.stderr
        assert lifecycle.status(settings)['api']['healthy']
    finally:
        stopped = run('stop.cmd')
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert lifecycle._load_state(settings) == {}
    assert not lifecycle._port_open(settings['api_port'])
