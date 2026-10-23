import json
from pathlib import Path

import pytest

from local_scrape.settings import DEFAULTS, load_settings


def test_credentials_are_installation_relative_not_cwd(tmp_path, monkeypatch):
    root = tmp_path / 'Application With Spaces'
    root.mkdir()
    (root / '.env').write_text('OLLAMA_API_KEY=fixture-root-key\n', encoding='utf-8')
    elsewhere = tmp_path / 'other'
    elsewhere.mkdir()
    (elsewhere / '.env').write_text('OLLAMA_API_KEY=wrong-cwd-key\n', encoding='utf-8')
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv('OLLAMA_API_KEY', raising=False)
    settings = load_settings(root=root)
    assert settings['model'] == 'deepseek-v4.1-flash'
    assert settings['ollama_api_key'] == 'fixture-root-key'
    assert DEFAULTS['credential_env_file'] == '.env'
    assert Path(settings['credential_env_file']) == root / '.env'


def test_environment_precedence_and_explicit_null(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('OLLAMA_API_KEY=fixture-file-key\n', encoding='utf-8')
    monkeypatch.setenv('OLLAMA_API_KEY', 'fixture-process-key')
    assert load_settings(root=tmp_path)['ollama_api_key'] == 'fixture-process-key'
    monkeypatch.setenv('OLLAMA_API_KEY', '')
    assert load_settings(root=tmp_path)['ollama_api_key'] is None
    monkeypatch.delenv('OLLAMA_API_KEY')
    assert load_settings(root=tmp_path, overrides={'credential_env_file': None})['ollama_api_key'] is None


def test_no_key_needed_and_external_credentials_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv('OLLAMA_API_KEY', raising=False)
    assert load_settings(root=tmp_path)['ollama_api_key'] is None
    with pytest.raises(ValueError, match='credential_env_file'):
        load_settings(root=tmp_path, overrides={'credential_env_file': '../external.env'})


def test_examples_have_no_personal_defaults():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'config.example.json').read_text(encoding='utf-8'))
    assert config['credential_env_file'] == '.env'
    assert config['model'] == 'deepseek-v4.1-flash'
    assert (root / '.env.example').read_text(encoding='utf-8').strip() == 'OLLAMA_API_KEY='
    for value in DEFAULTS.values():
        if isinstance(value, str):
            assert ':/' not in value.replace('https://', '').replace('http://', '')
