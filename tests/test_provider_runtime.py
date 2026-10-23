"""Provider-neutral lifecycle and offline health; no live inference."""
from pathlib import Path
import json
import httpx
import pytest
from local_scrape import lifecycle
from local_scrape.core import Engine
from local_scrape.settings import load_settings
from local_scrape.security import ScrapeError


def test_public_version_is_consistent():
    import local_scrape
    from local_scrape.service import create_app
    assert local_scrape.__version__ == '0.2.0'
    assert create_app().version == '0.2.0'


def test_child_only_carries_selected_credential_including_empty(tmp_path, monkeypatch):
    config = {'llm_provider': 'openai_compatible', 'model': 'org/model',
              'llm_base_url': 'http://localhost:1234/v1', 'llm_api_key_env': 'SELECTED_KEY'}
    (tmp_path / '.env').write_text('SELECTED_KEY=file-fixture\n')
    for name in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'OLLAMA_API_KEY', 'LLM_API_KEY'):
        monkeypatch.setenv(name, 'unrelated-fixture')
    monkeypatch.setenv('SELECTED_KEY', '')
    env = lifecycle.process_environment(load_settings(tmp_path, config))
    assert env['SELECTED_KEY'] == ''
    assert not any(name in env for name in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'OLLAMA_API_KEY', 'LLM_API_KEY'))
    monkeypatch.delenv('SELECTED_KEY')
    assert lifecycle.process_environment(load_settings(tmp_path, config))['SELECTED_KEY'] == 'file-fixture'
    disabled = lifecycle.process_environment(load_settings(tmp_path))
    assert 'SELECTED_KEY' not in disabled and 'OLLAMA_API_KEY' not in disabled


def test_child_empty_credential_cannot_revive_file_fallback(tmp_path, monkeypatch):
    import subprocess
    import sys
    config = {'llm_provider': 'openai', 'model': 'fixture/model', 'llm_api_key_env': 'SELECTED_KEY'}
    (tmp_path / 'config.json').write_text(json.dumps(config))
    (tmp_path / '.env').write_text('SELECTED_KEY=file-fixture\n')
    monkeypatch.setenv('SELECTED_KEY', '')
    settings = load_settings(tmp_path)
    package = str(Path(__file__).resolve().parents[1])
    code = (f'import sys; sys.path.insert(0, {package!r}); '
            'from local_scrape.settings import load_settings; '
            f'print(load_settings(root={str(tmp_path)!r})["llm_api_key"] is None)')
    result = subprocess.run([sys.executable, '-B', '-c', code], env=lifecycle.process_environment(settings),
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == 'True'


@pytest.mark.parametrize('provider', ['none', 'ollama', 'openai_compatible'])
def test_generic_lifecycle_never_starts_or_probes_llm(tmp_path, monkeypatch, provider):
    config = {'llm_provider': provider, 'model': '' if provider == 'none' else 'org/model',
              'llm_base_url': None if provider == 'none' else 'http://localhost:1234/v1',
              'ollama_mode': 'local'}
    settings = load_settings(tmp_path, config)
    python = tmp_path / '.venv/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(lifecycle, '_local_ollama_ready', lambda: pytest.fail('unexpected LLM probe'))
    monkeypatch.setattr(lifecycle, '_port_open', lambda port: False)
    monkeypatch.setattr(lifecycle, '_owned', lambda record: bool(record))
    monkeypatch.setattr(lifecycle, '_runtime_info', lambda _: {'pid': 999, 'instance_id': 'fixture'})
    spawned = []
    def spawn(settings, args, name, env):
        spawned.append(name)
        return {'pid': 999, 'instance_id': 'fixture'}
    monkeypatch.setattr(lifecycle, '_spawn', spawn)
    result = lifecycle._start_unlocked(settings)
    assert spawned == ['api']
    assert result['llm']['llm_provider'] == provider
    assert result['llm']['inference_verified'] is False
    assert 'ollama' not in result


@pytest.mark.parametrize('provider', ['none', 'openai'])
@pytest.mark.asyncio
async def test_default_engine_scrapes_without_ai_and_ai_fails_clearly(tmp_path, provider):
    settings = load_settings(tmp_path, {'allow_private_urls': True, 'respect_robots': False,
        'llm_provider': provider, 'model': '' if provider == 'none' else 'fixture/model'})
    settings['llm_api_key'] = None
    engine = Engine(settings)
    await engine.client.aclose()
    def source(request):
        assert 'authorization' not in request.headers
        assert request.headers['user-agent'] == 'LocalScrape/0.2'
        return httpx.Response(200, headers={'content-type': 'text/html'}, text='<title>Fixture</title><p>Unconfigured scraping works.</p>')
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(source))
    try:
        assert 'scraping works' in (await engine.scrape('http://127.0.0.1:1234', render='fast'))['text']
        with pytest.raises(ScrapeError) as err:
            await engine.extract('facts', text='source', schema={'type': 'object'})
        assert err.value.code == ('MODEL_NOT_CONFIGURED' if provider == 'none' else 'MODEL_AUTH_MISSING')
        with pytest.raises(ScrapeError) as err:
            await engine.schema('facts')
        assert err.value.code == ('MODEL_NOT_CONFIGURED' if provider == 'none' else 'MODEL_AUTH_MISSING')
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_health_is_config_only_and_never_probes_ai(tmp_path, monkeypatch):
    settings = load_settings(tmp_path, {'llm_provider': 'openai', 'model': 'org/model'})
    settings['llm_api_key'] = 'unverified-fixture'
    engine = Engine(settings)
    await engine.extractor.client.aclose()
    engine.extractor.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: pytest.fail('health must not contact provider')))
    try:
        result = await engine.health()
        assert result['llm_provider'] == 'openai'
        assert result['configured'] is True and result['auth_configured'] is True
        assert result['inference_verified'] is False
        assert 'unverified-fixture' not in json.dumps(result)
        assert 'ollama_available' not in result
    finally:
        await engine.close()
