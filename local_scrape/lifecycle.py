"""On-demand Windows lifecycle with PID + creation-time + image identity checks.

No global service registration, PATH changes, taskkill, or process-name matching.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import subprocess
import signal
import threading
import time
import uuid
from .settings import token_for
from .llm import normalize, public_status
from .workflows import WorkflowError


def _win_api():
    if os.name != 'nt':
        raise WorkflowError('UNSUPPORTED_PLATFORM','Managed lifecycle requires Windows',500)
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    api.OpenProcess.restype=wintypes.HANDLE
    api.CloseHandle.argtypes=[wintypes.HANDLE]
    api.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    api.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    api.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
    api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    api.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    return api


def _handle_identity(api,handle,pid):
    code=wintypes.DWORD()
    if not api.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value != 259:
        return None
    times=[wintypes.FILETIME() for _ in range(4)]
    size=wintypes.DWORD(32768); image=ctypes.create_unicode_buffer(size.value)
    if not api.GetProcessTimes(handle,*(ctypes.byref(t) for t in times)) or not api.QueryFullProcessImageNameW(handle,0,image,ctypes.byref(size)):
        return None
    created=(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return {'pid':pid,'creation':str(created),'executable':str(Path(image.value).resolve()).casefold()}


def _identity(pid):
    api=_win_api(); handle=api.OpenProcess(0x1000,False,int(pid))
    if not handle:
        return None
    try:
        return _handle_identity(api,handle,int(pid))
    finally:
        api.CloseHandle(handle)


def _same(record,identity):
    return bool(record and identity and all(record.get(k)==identity.get(k) for k in ('pid','creation','executable')))


def _owned(record):
    return bool(record and _same(record,_identity(record['pid'])))


def _terminate_owned(record):
    # Recheck on the SAME handle used for termination to prevent PID reuse races.
    api=_win_api(); handle=api.OpenProcess(0x1000|0x1|0x100000,False,int(record['pid']))
    if not handle:
        return False
    try:
        if not _same(record,_handle_identity(api,handle,int(record['pid']))):
            return False
        if not api.TerminateProcess(handle,0):
            raise WorkflowError('STOP_FAILED','Could not terminate the owned process',500)
        api.WaitForSingleObject(handle,10000)
        if _handle_identity(api,handle,int(record['pid'])) is not None:
            raise WorkflowError('STOP_FAILED','Owned process did not exit',500)
        return True
    finally:
        api.CloseHandle(handle)


def _state_path(settings):
    return Path(settings['data_dir'])/'processes.json'


def _load_state(settings):
    try:
        value=json.loads(_state_path(settings).read_text(encoding='utf-8'))
        if not isinstance(value,dict):
            raise ValueError()
        return value
    except FileNotFoundError:
        return {}
    except (ValueError,OSError):
        raise WorkflowError('INVALID_PROCESS_STATE','Process ownership file is invalid; no process will be stopped',500) from None


def _save_state(settings,value):
    path=_state_path(settings)
    tmp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value,indent=2),encoding='utf-8')
    tmp.replace(path)


@contextmanager
def _locked(settings):
    path=Path(settings['data_dir'])/'lifecycle.lock'
    owner=_identity(os.getpid())
    for _ in range(3):
        try:
            with path.open('x',encoding='utf-8') as out:
                json.dump(owner,out)
            break
        except FileExistsError:
            try:
                previous=json.loads(path.read_text(encoding='utf-8'))
            except (ValueError,OSError):
                raise WorkflowError('LIFECYCLE_BUSY','Lifecycle lock exists; retry after the other command completes',409) from None
            if _owned(previous):
                raise WorkflowError('LIFECYCLE_BUSY','Another lifecycle command is running',409)
            path.unlink(missing_ok=True)
    else:
        raise WorkflowError('LIFECYCLE_BUSY','Could not acquire lifecycle lock',409)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _runtime_info(settings):
    import httpx
    try:
        r=httpx.get(f"http://127.0.0.1:{settings['api_port']}/v1/runtime",headers={'Authorization':'Bearer '+token_for(settings)},timeout=2,trust_env=False)
        value=r.json()
        return value['data'] if r.status_code==200 and value.get('ok') else None
    except (httpx.HTTPError,ValueError,KeyError):
        return None


def _port_open(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1',port))==0


def _managed_ollama(settings):
    # Only explicit v0.1 local configs retain optional legacy server management.
    return bool(settings.get('_legacy_ollama') and settings.get('ollama_mode') == 'local')


def _local_ollama_ready():
    import httpx
    try:
        r=httpx.get('http://127.0.0.1:11434/api/version',timeout=2,trust_env=False)
        return r.status_code==200 and isinstance(r.json().get('version'),str)
    except (httpx.HTTPError,ValueError):
        return False


def process_environment(settings):
    root=Path(settings['root'])
    # Inherit only basic Windows execution variables and the explicitly named credential.
    names=('SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE')
    env={k:v for k,v in os.environ.items() if k.upper() in names}
    for name,relative in {'TEMP':'data/tmp','TMP':'data/tmp','HOME':'data/home','USERPROFILE':'data/home','APPDATA':'data/appdata','LOCALAPPDATA':'data/localappdata','XDG_CACHE_HOME':'.cache','XDG_CONFIG_HOME':'data/config','PYTHONPYCACHEPREFIX':'.cache/pycache','PLAYWRIGHT_BROWSERS_PATH':'browsers','OLLAMA_MODELS':'models','UV_CACHE_DIR':'.cache/uv','PIP_CACHE_DIR':'.cache/pip'}.items():
        path=root/relative; path.mkdir(parents=True,exist_ok=True); env[name]=str(path)
    env.update(LOCAL_SCRAPE_ROOT=str(root),PYTHONUNBUFFERED='1',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',OLLAMA_HOST='127.0.0.1:11434',OLLAMA_NUM_PARALLEL='1',OLLAMA_MAX_LOADED_MODELS='1',OLLAMA_NO_CLOUD='1')
    config = normalize(settings)
    if config['llm_provider'] != 'none':
        # Forward an explicit empty value too: otherwise child file fallback revives a disabled key.
        if not (config['_legacy_ollama'] and settings.get('ollama_mode') == 'local'):
            env[config['llm_api_key_env']] = config['llm_api_key'] or ''
    return env


def _publish_ownership(settings):
    """Publish from the actual interpreter, never the Windows venv redirector."""
    filename=os.environ.get('LOCAL_SCRAPE_HANDSHAKE')
    if not filename:
        return
    path=Path(filename).resolve()
    if path.parent != Path(settings['data_dir']).resolve():
        raise WorkflowError('INVALID_HANDSHAKE','Ownership handshake must be in the data directory',500)
    identity=_identity(os.getpid())
    if identity is None:
        raise WorkflowError('START_FAILED','Cannot establish interpreter identity',500)
    value={**identity,'instance_id':os.environ.get('LOCAL_SCRAPE_INSTANCE_ID',''),'parent_pid':os.getppid()}
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value),encoding='utf-8')
    tmp.replace(path)


def _spawn(settings,args,name,env):
    logdir=Path(settings['root'])/'logs'; logdir.mkdir(parents=True,exist_ok=True)
    env=dict(env)
    handshake=Path(settings['data_dir'])/('ownership-'+uuid.uuid4().hex+'.json')
    if name=='api':
        env['LOCAL_SCRAPE_HANDSHAKE']=str(handshake)
    with (logdir/f'{name}.log').open('ab',buffering=0) as logfile:
        child=subprocess.Popen(args,cwd=settings['root'],env=env,stdin=subprocess.DEVNULL,stdout=logfile,stderr=subprocess.STDOUT,close_fds=True,creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)
    launcher=_identity(child.pid)
    try:
        if name!='api':
            if launcher is not None:
                return {**launcher,'instance_id':env.get('LOCAL_SCRAPE_INSTANCE_ID','')}
        else:
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                if handshake.exists():
                    try:
                        record=json.loads(handshake.read_text(encoding='utf-8'))
                    except (PermissionError, FileNotFoundError):
                        # Windows scanners may briefly hold a newly renamed file.
                        time.sleep(.05)
                        continue
                    # Bind the nonce, live kernel identity and direct launch lineage.
                    if (record.get('instance_id')==env.get('LOCAL_SCRAPE_INSTANCE_ID')
                        and (record.get('pid')==child.pid or record.get('parent_pid')==child.pid)
                        and _owned(record)):
                        return record
                    break
                if child.poll() is not None:
                    break
                time.sleep(.05)
        raise WorkflowError('START_FAILED',f'{name} did not publish verified ownership; see logs/{name}.log',500)
    except Exception:
        if launcher and _owned(launcher):
            _terminate_owned(launcher)
        raise
    finally:
        handshake.unlink(missing_ok=True)


def status(settings):
    state=_load_state(settings); info=_runtime_info(settings)
    api=state.get('api')
    owned=_owned(api) if api else False
    healthy=bool(owned and info and api and info.get('instance_id')==api.get('instance_id') and info.get('pid')==api.get('pid'))
    return {'api':{'running':bool(info),'healthy':healthy,'owned':owned,'pid':api['pid'] if owned else None,'url':f"http://127.0.0.1:{settings['api_port']}"},
            'llm': public_status(normalize(settings))}


def start(settings):
    with _locked(settings):
        return _start_unlocked(settings)


def _start_unlocked(settings):
    state=_load_state(settings)
    current=status(settings)
    if current['api']['healthy'] and current['api']['owned']:
        return {**current,'already_running':True}
    if _port_open(settings['api_port']):
        raise WorkflowError('API_PORT_IN_USE','API port is in use by an unverified process; nothing was stopped',409)
    root=Path(settings['root']); python=root/'.venv/Scripts/python.exe'
    if not python.is_file():
        raise WorkflowError('RUNTIME_MISSING','Project .venv Python is missing; provision the runtime first',503)
    env=process_environment(settings)
    launched=[]
    try:
        if _managed_ollama(settings) and not _local_ollama_ready():
            if _port_open(11434):
                raise WorkflowError('OLLAMA_PORT_IN_USE','Ollama port belongs to an unverified listener',409)
            executable=root/'runtime/ollama/ollama.exe'
            if not executable.is_file():
                raise WorkflowError('RUNTIME_MISSING','Optional local Ollama binary is missing',503)
            state['ollama']=_spawn(settings,[str(executable),'serve'],'ollama',env)
            launched.append('ollama'); _save_state(settings,state)
            deadline=time.monotonic()+45
            while not _local_ollama_ready():
                if time.monotonic()>deadline or not _owned(state['ollama']):
                    raise WorkflowError('START_FAILED','Local Ollama did not become ready; see logs/ollama.log',503)
                time.sleep(.25)
        env['LOCAL_SCRAPE_INSTANCE_ID']=uuid.uuid4().hex
        state['api']=_spawn(settings,[str(python),'-m','local_scrape.service'],'api',env)
        launched.append('api'); _save_state(settings,state)
        deadline=time.monotonic()+90
        while True:
            info=_runtime_info(settings)
            if info and info.get('pid')==state['api']['pid'] and info.get('instance_id')==state['api']['instance_id']:
                break
            if time.monotonic()>deadline or not _owned(state['api']):
                raise WorkflowError('START_FAILED','API did not become ready; see logs/api.log',503)
            time.sleep(.25)
        return {**status(settings),'already_running':False}
    except Exception:
        # Roll back ONLY processes launched by this invocation and still proven owned.
        for name in reversed(launched):
            if _owned(state.get(name)):
                _terminate_owned(state[name])
            state.pop(name,None)
        _save_state(settings,state)
        raise

def stop(settings):
    with _locked(settings):
        return _stop_unlocked(settings)


def _stop_unlocked(settings):
    state=_load_state(settings); stopped=[]; skipped=[]
    names=['api']+(['ollama'] if _managed_ollama(settings) else [])
    for name in names:
        record=state.get(name)
        if not record:
            continue
        if not _owned(record):
            skipped.append(name+': stale or unowned PID; not terminated')
            state.pop(name,None)
            continue
        if name=='api':
            info=_runtime_info(settings)
            if info and info.get('pid')==record['pid'] and info.get('instance_id')==record.get('instance_id'):
                import httpx
                try:
                    httpx.post(f"http://127.0.0.1:{settings['api_port']}/v1/runtime/stop",headers={'Authorization':'Bearer '+token_for(settings)},timeout=3,trust_env=False)
                except httpx.HTTPError:
                    pass
                deadline=time.monotonic()+15
                while _owned(record) and time.monotonic()<deadline:
                    time.sleep(.2)
        if _owned(record):
            _terminate_owned(record)
        if _owned(record):
            raise WorkflowError('STOP_FAILED',f'Owned {name} process remains active',500)
        stopped.append(name); state.pop(name,None)
    _save_state(settings,state)
    return {'stopped':stopped,'skipped':skipped}


@contextmanager
def _deferred_interrupts():
    """Finish ownership bookkeeping before delivering console cancellation."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    pending = []
    signals = [signal.SIGINT]
    if hasattr(signal, 'SIGBREAK'):
        signals.append(signal.SIGBREAK)
    previous = {item: signal.signal(item, lambda signum, frame: pending.append(signum)) for item in signals}
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)
        if pending and not failed:
            raise KeyboardInterrupt


@contextmanager
def task_service(settings):
    """An exclusive foreground lease; never reuse or stop a pre-existing service."""
    with _locked(settings):
        state = _load_state(settings)
        if any(_owned(record) for record in state.values() if isinstance(record, dict) and 'pid' in record):
            raise WorkflowError('TASK_SERVICE_BUSY', 'A service is already running; task mode will not reuse or stop it', 409)
        if _port_open(settings['api_port']):
            raise WorkflowError('API_PORT_IN_USE', 'API port is in use; nothing was stopped', 409)
        if _managed_ollama(settings) and _port_open(11434):
            raise WorkflowError('OLLAMA_PORT_IN_USE', 'Task mode will not reuse an existing local model service', 409)
        started = False
        try:
            with _deferred_interrupts():
                _start_unlocked(settings)
                started = True
            yield
        finally:
            if started:
                with _deferred_interrupts():
                    _stop_unlocked(settings)
