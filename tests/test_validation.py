"""Real child processes; no provider/network calls."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import httpx
import pytest

from local_scrape.extraction import Extractor
from local_scrape.security import ScrapeError


@pytest.mark.asyncio
async def test_schema_generation_does_not_repair_worker_timeouts(monkeypatch):
    import local_scrape.extraction as extraction
    calls = []

    async def timeout(*args, **kwargs):
        raise ScrapeError('VALIDATION_TIMEOUT', 'Validation deadline', 504)

    def serve(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"type":"string"}'}}]})

    monkeypatch.setattr(extraction, 'validate', timeout)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        adapter = Extractor({'ollama_api_key': 'test-only'}, client=client)
        with pytest.raises(ScrapeError) as error:
            await adapter.schema('a string')
        assert error.value.code == 'VALIDATION_TIMEOUT'
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_regex_validation_has_deadline_and_keeps_loop_responsive():
    # Bounded even on the old implementation (~1s per validation), catastrophic
    # enough to distinguish event-loop execution from isolated validation.
    raw = json.dumps('a' * 24 + '!')
    ticks = []
    running = True

    async def heartbeat():
        while running:
            ticks.append(time.monotonic())
            await asyncio.sleep(.02)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'choices': [{'message': {'content': raw}}]})
    )) as client:
        adapter = Extractor({'ollama_api_key': 'test-only', 'validation_timeout_seconds': .5}, client=client)
        ticker = asyncio.create_task(heartbeat())
        started = time.monotonic()
        try:
            with pytest.raises(ScrapeError) as error:
                await adapter.extract('facts', 'source', {'type': 'string', 'pattern': '(a+)+$'})
            assert error.value.code == 'VALIDATION_TIMEOUT'
        finally:
            running = False
            await ticker
        assert time.monotonic() - started < 2.5
        assert len(ticks) > 5
        assert max(b - a for a, b in zip(ticks, ticks[1:])) < .3


def record_children(monkeypatch):
    from local_scrape import validation_worker as worker
    original = worker.subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(worker.subprocess, 'Popen', spawn)
    return children


def assert_reaped(children):
    assert children
    for child in children:
        assert child.poll() is not None
        assert child.stdin.closed and child.stdout.closed
        if os.name == 'nt':
            # Query the same retained Windows kernel handle, not a potentially
            # reused PID. STILL_ACTIVE is 259.
            import ctypes
            from ctypes import wintypes
            code = wintypes.DWORD()
            query = ctypes.WinDLL('kernel32', use_last_error=True).GetExitCodeProcess
            query.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            assert query(wintypes.HANDLE(int(child._handle)), ctypes.byref(code))
            assert code.value != 259
    assert not any(t.name == 'schema-validation-pipes' for t in threading.enumerate())


@pytest.mark.asyncio
async def test_real_api_health_responds_during_regex_validation(tmp_path, monkeypatch):
    from local_scrape.service import create_app
    from local_scrape.settings import load_settings, token_for

    children = record_children(monkeypatch)
    config = load_settings(root=tmp_path, overrides={'credential_env_file': None})
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'choices': [{'message': {'content': json.dumps('a' * 100 + '!')}}]})
    )) as model_client:
        adapter = Extractor({'ollama_api_key': 'test-only', 'validation_timeout_seconds': 1.5}, client=model_client)
        class FixtureEngine:
            async def extract(self, prompt, **kwargs):
                return await adapter.extract(prompt, kwargs['text'], kwargs['schema'])

            async def close(self):
                await adapter.close()

        app = create_app(config, engine_factory=lambda settings: FixtureEngine(), run_scheduler=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test-only') as api:
                request = asyncio.create_task(api.post('/v1/extract', headers={'Authorization': 'Bearer ' + token_for(config)},
                    json={'prompt': 'facts', 'text': 'source', 'schema': {'type': 'string', 'pattern': '(a+)+$'}}))
                try:
                    async with asyncio.timeout(3):
                        while len(children) < 2:
                            await asyncio.sleep(.01)
                    await asyncio.sleep(.2)
                    latencies = []
                    for _ in range(5):
                        started = time.monotonic()
                        health = await asyncio.wait_for(api.get('/health'), timeout=.3)
                        latencies.append(time.monotonic() - started)
                        assert health.status_code == 200
                        assert not request.done()
                    response = await request
                    assert response.status_code == 504
                    assert response.json()['error']['code'] == 'VALIDATION_TIMEOUT'
                    json.dumps(response.json(), allow_nan=False)
                    print(f'API health max latency during regex validation: {max(latencies):.3f}s')
                finally:
                    if not request.done():
                        request.cancel()
                        await asyncio.gather(request, return_exceptions=True)
    assert_reaped(children)


@pytest.mark.asyncio
async def test_catastrophic_regex_child_is_killed_reaped_and_lock_released(monkeypatch):
    children = record_children(monkeypatch)
    calls = []

    def serve(request):
        calls.append(request)
        raw = json.dumps('a' * 100 + '!') if len(calls) == 1 else '"valid"'
        return httpx.Response(200, json={'choices': [{'message': {'content': raw}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        adapter = Extractor({'ollama_api_key': 'test-only', 'validation_timeout_seconds': 1}, client=client)
        started = time.monotonic()
        with pytest.raises(ScrapeError) as error:
            await adapter.extract('facts', 'source', {'type': 'string', 'pattern': '(a+)+$'})
        elapsed = time.monotonic() - started
        assert error.value.code == 'VALIDATION_TIMEOUT'
        assert elapsed < 2.5
        assert len(calls) == 1  # deadlines are infrastructure failures, not repair prompts
        assert_reaped(children)
        assert not adapter.lock.locked()
        assert await adapter.extract('facts', 'source', {'type': 'string'}) == 'valid'
    assert_reaped(children)
    print(f'catastrophic regex terminated and reaped in {elapsed:.3f}s')


@pytest.mark.asyncio
async def test_cancel_validation_kills_and_reaps_owned_child(monkeypatch):
    from local_scrape.validation_worker import validate
    children = record_children(monkeypatch)
    task = asyncio.create_task(validate({'pattern': '(a+)+$'}, 'a' * 100 + '!'))
    try:
        async with asyncio.timeout(2):
            while not children:
                await asyncio.sleep(.01)
        await asyncio.sleep(.25)
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < 1
        assert_reaped(children)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_real_base_interpreter_identity_and_stripped_environment(monkeypatch):
    from local_scrape.validation_worker import _launch_spec
    # Deliberately fake markers; never read or display actual provider secrets.
    for name in ('OLLAMA_API_KEY', 'OPENAI_API_KEY', 'LOCAL_SCRAPE_TOKEN', 'PYTHONPATH', 'PYTHONSTARTUP'):
        monkeypatch.setenv(name, 'test-only-never-inherit')
    command, env, cwd = _launch_spec()
    assert Path(command[0]).resolve() == Path(sys._base_executable).resolve()
    assert set(env) <= {'SystemRoot', 'WINDIR', 'SYSTEMDRIVE', 'PYTHONPATH', 'PYTHONIOENCODING', 'PYTHONDONTWRITEBYTECODE'}
    assert 'test-only-never-inherit' not in json.dumps(env)
    assert all(Path(path).is_absolute() for path in env['PYTHONPATH'].split(os.pathsep))
    probe = (
        'import os,json; '
        'assert not any(k in os.environ for k in '
        '("OLLAMA_API_KEY","OPENAI_API_KEY","LOCAL_SCRAPE_TOKEN","PYTHONSTARTUP")); '
        'assert "test-only-never-inherit" not in os.environ.get("PYTHONPATH", ""); '
        'print(json.dumps({"pid":os.getpid()}))'
    )
    with subprocess.Popen(command[:-1] + ['-c', probe], env=env, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
        output, _ = child.communicate(timeout=5)
        assert child.returncode == 0
        assert json.loads(output)['pid'] == child.pid


@pytest.mark.parametrize('schema, data', [
    ({'type': 'number'}, 1.5),
    ({'$defs': {'n': {'type': 'integer'}}, '$ref': '#/$defs/n'}, 3),
    ({'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}, {'name': 'test'}),
])
@pytest.mark.asyncio
async def test_worker_accepts_ordinary_schemas(schema, data):
    from local_scrape.validation_worker import validate
    await validate(schema, data)
    json.dumps(data, allow_nan=False)


@pytest.mark.parametrize('schema, data, code', [
    ({'type': 'nonsense'}, None, 'INVALID_SCHEMA'),
    ({'pattern': '['}, None, 'INVALID_SCHEMA'),
    ({'$ref': '#/$defs/missing'}, {}, 'INVALID_SCHEMA'),
    ({'$ref': '#'}, {}, 'INVALID_SCHEMA'),
    ({'$dynamicRef': 'file:///test-only-schema.json'}, {}, 'INVALID_SCHEMA'),
    ({'type': 'integer'}, 'test-only', None),
])
@pytest.mark.asyncio
async def test_worker_rejects_invalid_schema_or_data(schema, data, code):
    from local_scrape.validation_worker import validate
    if code:
        with pytest.raises(ScrapeError) as error:
            await validate(schema, data)
        assert error.value.code == code
    else:
        with pytest.raises(ValueError):
            await validate(schema, data)


@pytest.mark.parametrize('kind', ['bytes', 'unicode_bytes', 'depth', 'width', 'cycle', 'nonfinite', 'external_ref'])
def test_schema_preflight_bounds_before_metaschema_work(kind, monkeypatch):
    from local_scrape.extraction import check_schema
    from local_scrape.validation_worker import MAX_DEPTH, MAX_NODES, SCHEMA_BYTES
    import jsonschema

    def forbidden(*args, **kwargs):
        pytest.fail('Synchronous metaschema validation must not run')

    monkeypatch.setattr(jsonschema.Draft202012Validator, 'check_schema', forbidden)
    if kind == 'bytes':
        schema = {'description': 'x' * SCHEMA_BYTES}
    elif kind == 'unicode_bytes':
        schema = {'description': '\U0001f600' * (SCHEMA_BYTES // 6)}
    elif kind == 'depth':
        schema = {}
        for _ in range(MAX_DEPTH + 1):
            schema = {'not': schema}
    elif kind == 'width':
        schema = {'enum': [0] * (MAX_NODES + 1)}
    elif kind == 'cycle':
        schema = {}
        schema['not'] = schema
    elif kind == 'nonfinite':
        schema = {'minimum': float('inf')}
    else:
        schema = {'$defs': {'evil': {'$ref': 'https://example.invalid/schema'}}}
    started = time.monotonic()
    with pytest.raises(ScrapeError) as error:
        check_schema(schema)
    assert error.value.code == 'INVALID_SCHEMA'
    assert time.monotonic() - started < .2


def test_cheap_preflight_defers_metaschema_and_regex_work(monkeypatch):
    from local_scrape.extraction import check_schema
    import jsonschema

    def forbidden(*args, **kwargs):
        pytest.fail('Synchronous metaschema validation must not run')

    monkeypatch.setattr(jsonschema.Draft202012Validator, 'check_schema', forbidden)
    schema = {'pattern': '(a+)+$'}
    assert check_schema(schema) is schema


@pytest.mark.parametrize('data', [float('inf'), {'nested': [float('nan')]}, 'x' * 1_000_001, [0] * 20_001],
                         ids=['infinity', 'nested_nan', 'oversize_bytes', 'oversize_nodes'])
@pytest.mark.asyncio
async def test_data_limits_reject_before_launch(data, monkeypatch):
    from local_scrape import validation_worker as worker

    def forbidden(*args, **kwargs):
        pytest.fail('Invalid or oversized input must not launch a child')

    monkeypatch.setattr(worker.subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError):
        await worker.validate({}, data)
