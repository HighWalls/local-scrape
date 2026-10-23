"""Provider contract tests: HTTP fixtures use MockTransport, never live inference."""
import asyncio
import json
import httpx
import pytest

from local_scrape.settings import load_settings
from local_scrape.extraction import Extractor
from local_scrape.security import ScrapeError


def test_default_is_provider_free_and_generic_model_is_opaque(tmp_path):
    settings = load_settings(root=tmp_path)
    assert settings['llm_provider'] == 'none'
    assert settings['model'] == ''
    assert settings['llm_api_key'] is None
    model = 'org/model:version@cloud'
    configured = load_settings(root=tmp_path, overrides={'llm_provider': 'openai_compatible',
        'llm_base_url': 'http://localhost:12345/proxy/v1', 'model': model})
    assert configured['model'] == model
    assert configured['llm_base_url'] == 'http://localhost:12345/proxy/v1'


def test_selected_credential_precedence_and_plaintext_rejection(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('MY_MODEL_KEY=file-fixture\nOTHER_KEY=do-not-read\n')
    config = {'llm_provider': 'openai', 'model': 'opaque/model', 'llm_api_key_env': 'MY_MODEL_KEY'}
    monkeypatch.delenv('MY_MODEL_KEY', raising=False)
    assert load_settings(tmp_path, config)['llm_api_key'] == 'file-fixture'
    monkeypatch.setenv('MY_MODEL_KEY', 'process-fixture')
    assert load_settings(tmp_path, config)['llm_api_key'] == 'process-fixture'
    monkeypatch.setenv('MY_MODEL_KEY', '')
    assert load_settings(tmp_path, config)['llm_api_key'] is None
    monkeypatch.delenv('MY_MODEL_KEY')
    assert load_settings(tmp_path, {**config, 'credential_env_file': None})['llm_api_key'] is None
    (tmp_path / 'config.json').write_text(json.dumps({'llm_api_key': 'plaintext-rejected'}))
    with pytest.raises(ValueError, match='Unknown settings'):
        load_settings(tmp_path)


@pytest.mark.parametrize('url', ['http://remote.example/v1', 'https://user:pass@host/v1',
    'https://host/v1?key=secret', 'https://host/v1#secret', 'https://host:bad/v1',
    'https://host:65536/v1', 'https://host/\npath', 'https://host\\evil/v1', 'https://host/v1?',
    'https://host/v1#', 'https://[::1]garbage', 'https://[::1]garbage:443', 'https://host:'])
def test_generic_endpoint_rejects_unsafe_config(tmp_path, url):
    with pytest.raises(ValueError):
        load_settings(tmp_path, {'llm_provider': 'openai_compatible', 'model': 'x', 'llm_base_url': url})


@pytest.mark.asyncio
async def test_openai_chat_contract_and_disabled_default():
    calls = []
    def fixture(request):
        calls.append(request)
        assert str(request.url) == 'https://api.openai.com/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer fixture-key'
        body = json.loads(request.content)
        assert body['model'] == 'org/model:reasoning@x'
        assert body['max_completion_tokens'] == 1234
        assert 'max_tokens' not in body and 'temperature' not in body
        assert body['messages'] == [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'source'}]
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture)) as client:
        adapter = Extractor({'llm_provider': 'openai', 'model': 'org/model:reasoning@x',
            'llm_api_key': 'fixture-key', 'llm_max_tokens': 1234}, client)
        assert await adapter._chat([{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'source'}], {}) == '{}'
        disabled = Extractor({}, client)
        with pytest.raises(ScrapeError) as err:
            await disabled._chat([], {})
        assert err.value.code == 'MODEL_NOT_CONFIGURED'
    assert len(calls) == 1


@pytest.mark.parametrize('provider,base,expected_url', [
    ('anthropic', None, 'https://api.anthropic.com/v1/messages'),
    ('gemini', None, 'https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent'),
    ('openai_compatible', 'http://[::1]:12345/proxy/v1/', 'http://[::1]:12345/proxy/v1/chat/completions'),
    ('openai_compatible', 'https://router.example/api/v1', 'https://router.example/api/v1/chat/completions'),
    ('ollama', 'http://localhost:23456/prefix', 'http://localhost:23456/prefix/api/chat'),
])
@pytest.mark.asyncio
async def test_native_adapter_contracts(provider, base, expected_url):
    messages = [{'role': 'system', 'content': 'system-instructions'}, {'role': 'user', 'content': 'source'}]
    config = {'llm_provider': provider, 'model': 'models/gemini-test' if provider == 'gemini' else 'org/model@version',
              'llm_api_key': 'fixture-secret', 'llm_max_tokens': 321}
    if base is not None:
        config['llm_base_url'] = base
    def fixture(request):
        assert str(request.url) == expected_url
        body = json.loads(request.content)
        assert 'temperature' not in body
        if provider == 'anthropic':
            assert request.headers['x-api-key'] == 'fixture-secret'
            assert request.headers['anthropic-version'] == '2023-06-01'
            assert 'authorization' not in request.headers
            assert body == {'model': 'org/model@version', 'system': 'system-instructions',
                            'messages': messages[1:], 'max_tokens': 321, 'stream': False}
            return httpx.Response(200, json={'content': [{'type': 'thinking', 'thinking': 'never-output'},
                {'type': 'text', 'text': '{'}, {'type': 'text', 'text': '}'}]})
        if provider == 'gemini':
            assert request.headers['x-goog-api-key'] == 'fixture-secret'
            assert 'authorization' not in request.headers and not request.url.query
            assert body == {'systemInstruction': {'parts': [{'text': 'system-instructions'}]},
                'contents': [{'role': 'user', 'parts': [{'text': 'source'}]}],
                'generationConfig': {'maxOutputTokens': 321}}
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [
                {'thought': True, 'text': 'never-output'}, {'text': '{'}, {'text': '}'}]}}]})
        assert request.headers['authorization'] == 'Bearer fixture-secret'
        if provider == 'ollama':
            assert body['format'] == {'type': 'object'} and body['options']['num_predict'] == 321
            return httpx.Response(200, json={'message': {'content': '{}'}})
        assert body['model'] == 'org/model@version' and body['max_tokens'] == 321
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture)) as client:
        adapter = Extractor(config, client)
        assert await adapter._chat(messages, {'type': 'object'}) == '{}'
        assert adapter.config['model'] == config['model']


@pytest.mark.parametrize('model', ['gemini-test', 'models/gemini-test'])
@pytest.mark.asyncio
async def test_gemini_model_prefix_is_not_duplicated(model):
    def fixture(request):
        assert request.url.path == '/v1beta/models/gemini-test:generateContent'
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': '{}'}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture)) as client:
        assert await Extractor({'llm_provider': 'gemini', 'model': model, 'llm_api_key': 'fixture'}, client)._chat([], {}) == '{}'


def test_provider_model_and_base_are_required(tmp_path):
    for config in ({'llm_provider': 'invented', 'model': 'x'}, {'llm_provider': 'openai'},
                   {'llm_provider': 'openai_compatible', 'model': 'x'}):
        with pytest.raises(ValueError):
            load_settings(tmp_path, config)
