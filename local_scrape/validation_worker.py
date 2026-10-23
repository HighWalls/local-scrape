"""Bounded validation of untrusted JSON Schema in a disposable interpreter.

Only cheap structural/size preflight runs in the caller. Regex compilation,
metaschema checks and instance validation run in the killable child. The
supervisor thread does *not* execute schema code. No inherited credentials,
site initialization, argv payloads, temporary files or remote ref retrieval.
"""
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import threading
import time


SCHEMA_BYTES = 128_000
DATA_BYTES = 1_000_000
MAX_DEPTH = 64
MAX_NODES = 20_000
DEFAULT_TIMEOUT = 3.0


def bounded_json(value, *, schema=False):
    """Reject oversized, cyclic, non-JSON and non-finite trees before encoding."""
    limit = SCHEMA_BYTES if schema else DATA_BYTES
    stack = [(value, 0)]
    nodes = 0
    characters = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError('JSON complexity limit exceeded')
        if isinstance(item, dict):
            # Bound width before allocating work, including object key work.
            if len(item) > MAX_NODES - nodes - len(stack):
                raise ValueError('JSON complexity limit exceeded')
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError('JSON object keys must be strings')
                characters += len(key)
                if schema and key in ('$ref', '$dynamicRef') and (
                    not isinstance(child, str) or not child.startswith('#')
                ):
                    raise ValueError('External schema references are forbidden')
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > MAX_NODES - nodes - len(stack):
                raise ValueError('JSON complexity limit exceeded')
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            characters += len(item)
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError('Non-finite number')
        elif item is not None and not isinstance(item, (bool, int)):
            raise ValueError('Value is not JSON')
        if characters > limit:
            raise ValueError('JSON byte limit exceeded')
    encoded = json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    if len(encoded) > limit:
        raise ValueError('JSON byte limit exceeded')
    return encoded


def _launch_spec():
    # Windows venv python.exe is a launcher: killing its PID can orphan the
    # actual interpreter. Launch the base executable directly, with only the
    # package source and this environment's dependency directories available.
    executable = Path(sys._base_executable).resolve(strict=True)
    root = Path(__file__).resolve().parent.parent
    paths = dict.fromkeys([str(root), sysconfig.get_path('purelib'), sysconfig.get_path('platlib')])
    env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'SYSTEMDRIVE') if key in os.environ}
    env.update(PYTHONPATH=os.pathsep.join(paths), PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    return [str(executable), '-S', '-P', '-u', str(Path(__file__).resolve())], env, str(root)


def _run_worker(payload, timeout, stop):
    command, env, cwd = _launch_spec()
    deadline = time.monotonic() + timeout
    options = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                   env=env, cwd=cwd, close_fds=True)
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    with subprocess.Popen(command, **options) as child:
        result = {}

        def exchange():
            try:
                result['stdout'] = child.communicate(payload)[0]
            except (OSError, ValueError):
                result['failed'] = True

        # Keep pipe writes outside the supervisor too: on Windows a pipe write
        # can block before communicate's own timeout starts being enforced.
        io = threading.Thread(target=exchange, name='schema-validation-pipes')
        io.start()
        try:
            while True:
                if stop.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError()
                try:
                    child.wait(timeout=min(.025, max(.001, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if child.poll() is None:
                child.kill()  # exact retained Popen handle, never a PID search
            child.wait()
            io.join()  # child owns no descendants; exit closes all pipe writers
        if child.returncode or result.get('failed'):
            raise RuntimeError('Validation worker failed')
        output = result.get('stdout', b'')
        if len(output) > 4096:
            raise RuntimeError('Invalid validation worker response')
        response = json.loads(output)
        if response.get('pid') != child.pid:
            raise RuntimeError('Validation worker identity mismatch')
        return response['status']


async def validate(schema, data=None, *, schema_only=False, timeout=DEFAULT_TIMEOUT):
    """Validate off-loop; deadline/cancellation always kills and reaps the child."""
    from .security import ScrapeError

    try:
        encoded_schema = bounded_json(schema, schema=True)
    except (ValueError, RecursionError, OverflowError):
        raise ScrapeError('INVALID_SCHEMA', 'Schema exceeds JSON safety limits or contains external references', 422) from None
    if not isinstance(schema, dict):
        raise ScrapeError('INVALID_SCHEMA', 'Schema must be a JSON Schema object', 422)
    encoded_data = b'null' if schema_only else bounded_json(data)
    payload = b'{"schema":' + encoded_schema + b',"data":' + encoded_data + b',"schema_only":' + (b'true' if schema_only else b'false') + b'}'
    timeout = max(.05, min(float(timeout), DEFAULT_TIMEOUT))
    stop = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_run_worker, payload, timeout, stop))
    try:
        status = await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        # A canceled HTTP request must not leave a worker or pending pipe task.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()  # consume cleanup/timeout errors, never log payloads
        raise
    except TimeoutError:
        raise ScrapeError('VALIDATION_TIMEOUT', 'JSON Schema validation exceeded its deadline', 504) from None
    except (OSError, RuntimeError, ValueError, KeyError):
        raise ScrapeError('VALIDATION_UNAVAILABLE', 'JSON Schema validation worker failed', 503) from None
    if status == 'invalid_schema':
        raise ScrapeError('INVALID_SCHEMA', 'Invalid or unresolvable Draft 2020-12 JSON Schema', 422)
    if status == 'invalid_data':
        raise ValueError('Model output failed JSON Schema validation')
    if status != 'ok':
        raise ScrapeError('VALIDATION_UNAVAILABLE', 'JSON Schema validation worker failed', 503)


def _main():
    # Drain stdin before loading dependencies, never echo inputs or exceptions.
    payload = sys.stdin.buffer.read(SCHEMA_BYTES + DATA_BYTES + 1024)
    from jsonschema import Draft202012Validator, SchemaError, ValidationError
    from referencing import Registry
    from referencing.exceptions import NoSuchResource, Unresolvable

    def refuse_remote(uri):
        raise NoSuchResource(ref=uri)

    status = 'failed'
    try:
        request = json.loads(payload)
        schema = request['schema']
        bounded_json(schema, schema=True)
        bounded_json(request['data'])
        Draft202012Validator.check_schema(schema)
        if not request['schema_only']:
            Draft202012Validator(schema, registry=Registry(retrieve=refuse_remote)).validate(request['data'])
        status = 'ok'
    except (SchemaError, Unresolvable, RecursionError):
        status = 'invalid_schema'
    except ValidationError:
        status = 'invalid_data'
    except (ValueError, TypeError, KeyError, OverflowError):
        status = 'invalid_schema'
    print(json.dumps({'status': status, 'pid': os.getpid()}, allow_nan=False))


if __name__ == '__main__':
    _main()
