import json
import httpx
import pytest
from local_scrape.extraction import Extractor, parse_json
from local_scrape.security import ScrapeError


@pytest.mark.parametrize('raw', ['1e10000', '{"items":[1,-1e10000]}', 'NaN', 'Infinity', '-Infinity'])
def test_parse_rejects_all_nonfinite_numbers(raw):
    with pytest.raises(ValueError):
        parse_json(raw)


def test_parse_finite_exponents_are_strict_json_serializable():
    value = parse_json('{"small":1e-100,"large":1e100}')
    assert value == {'small': 1e-100, 'large': 1e100}
    json.dumps(value, allow_nan=False)


@pytest.mark.parametrize('bad, repaired, schema', [
    ('1e10000', '1e100', {'type': 'number'}),
    ('{"items":[1,-1e10000]}', '{"items":[1,-1e100]}', {'type': 'object'}),
])
@pytest.mark.asyncio
async def test_numeric_overflow_uses_existing_one_repair(bad, repaired, schema):
    calls = []

    def serve(request):
        calls.append(request)
        content = bad if len(calls) == 1 else repaired
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        adapter = Extractor({'ollama_api_key': 'test-only'}, client=client)
        data = await adapter.extract('numbers', 'source', schema)
    assert data == json.loads(repaired)
    assert len(calls) == 2
    json.dumps({'ok': True, 'data': data}, allow_nan=False)


@pytest.mark.asyncio
async def test_persistent_overflow_is_rejected_after_one_repair():
    calls = []

    def serve(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '1e10000'}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        adapter = Extractor({'ollama_api_key': 'test-only'}, client=client)
        with pytest.raises(ScrapeError) as error:
            await adapter.extract('number', 'source', {'type': 'number'})
    assert error.value.code == 'MODEL_VALIDATION_FAILED'
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_cloud_repairs_invalid_numeric_type_without_native_format():
    payloads = []
    async def serve(request):
        assert request.url == 'https://ollama.com/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-only'
        body = json.loads(request.content)
        payloads.append(body)
        assert 'format' not in body and 'response_format' not in body
        content = '{"price":"24.5"}' if len(payloads) == 1 else '```json\n{"price":24.5}\n```'
        return httpx.Response(200,json={'choices':[{'message':{'content':content}}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    adapter = Extractor({'ollama_mode':'cloud','ollama_url':'https://ollama.com','ollama_api_key':'test-only','model':'gemma4:31b'}, client=client)
    data = await adapter.extract('Price', 'Kettle costs 24.5', {'type':'object','properties':{'price':{'type':'number'}},'required':['price']})
    assert data == {'price':24.5}
    assert len(payloads) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_cloud_validation_retry_is_bounded():
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"price":"wrong"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        adapter = Extractor({'ollama_api_key':'test-only'},client=client)
        with pytest.raises(ScrapeError) as error:
            await adapter.extract('price','price absent',{'type':'object','properties':{'price':{'type':'number'}}})
        assert error.value.code == 'MODEL_VALIDATION_FAILED'
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_local_uses_native_format_without_cloud_auth():
    def serve(request):
        body = json.loads(request.content)
        assert request.url.path == '/api/chat'
        assert body['format'] == {'type':'object'}
        assert body['think'] is False and body['stream'] is False
        assert 'authorization' not in request.headers
        return httpx.Response(200,json={'message':{'content':'{}'}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        adapter = Extractor({'ollama_mode':'local','ollama_url':'http://127.0.0.1:11434','ollama_api_key':'never-forward'},client=client)
        assert await adapter.extract('facts','source',{'type':'object'}) == {}


@pytest.mark.parametrize('url',['https://ollama.com.evil.test','http://ollama.com','https://user:pass@ollama.com','https://ollama.com/path'])
def test_cloud_endpoint_whitelist(url):
    with pytest.raises(ScrapeError):
        Extractor({'ollama_url':url})


@pytest.mark.asyncio
async def test_external_schema_reference_is_rejected_without_network():
    from local_scrape.extraction import check_schema
    with pytest.raises(ScrapeError) as error:
        check_schema({'$ref':'https://example.com/schema'})
    assert error.value.code == 'INVALID_SCHEMA'


@pytest.mark.asyncio
async def test_unresolvable_internal_reference_returns_useful_validation_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]}))) as client:
        adapter = Extractor({'ollama_api_key':'test-only'},client=client)
        with pytest.raises(ScrapeError) as error:
            await adapter.extract('facts','source',{'$ref':'#/$defs/missing'})
        assert error.value.code == 'INVALID_SCHEMA'


@pytest.mark.asyncio
async def test_schema_generation_rejects_shorthand_and_returns_schema_itself():
    calls = []
    def serve(request):
        calls.append(request)
        content = '{"product":{"name":"string"}}' if len(calls) == 1 else '{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}'
        return httpx.Response(200,json={'choices':[{'message':{'content':content}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        adapter = Extractor({'ollama_api_key':'test-only'},client=client)
        schema = await adapter.schema('Product with a name string')
        assert schema['type'] == 'object'
        assert schema['properties']['name']['type'] == 'string'
        assert len(calls) == 2
