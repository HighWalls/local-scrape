import json
import os
from pathlib import Path
import sys
import subprocess

import pytest
from local_scrape.cli import main
from local_scrape import lifecycle
from local_scrape.settings import load_settings


def test_cli_machine_json_and_exit_codes(tmp_path,capsys):
    seen=[]
    def request(settings,method,path,payload=None):
        seen.append((method,path,payload))
        return {'ok':True,'data':{'text':'actual'}}
    assert main(['--root',str(tmp_path),'scrape','https://example.com','--json'],request=request)==0
    assert json.loads(capsys.readouterr().out)['data']['text']=='actual'
    assert seen[0][0:2]==('POST','/v1/scrape')
    def fail(*a,**k): return {'ok':False,'error':{'code':'FETCH_FAILED','message':'blocked','details':{}}}
    assert main(['--root',str(tmp_path),'scrape','https://example.com','--json'],request=fail)==1
    assert json.loads(capsys.readouterr().out)['ok'] is False
    assert main(['scrape','--max-pages','3'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='INVALID_ARGUMENT'


def test_cli_extract_file_and_monitor_routing(tmp_path,capsys):
    text=tmp_path/'input.txt'; text.write_text('document',encoding='utf-8')
    schema=tmp_path/'schema.json'; schema.write_text('{"type":"object"}',encoding='utf-8')
    seen=[]
    def req(settings,method,path,payload=None):
        seen.append((method,path,payload)); return {'ok':True,'data':{}}
    args=['--root',str(tmp_path)]
    assert main(args+['extract','--prompt','names','--text-file',str(text),'--schema-file',str(schema)],request=req)==0
    assert seen[-1][2]['text']=='document' and seen[-1][2]['schema']=={'type':'object'}
    for tail,method,path in [(['monitor','create','https://example.com'],'POST','/v1/monitors'),(['monitor','check','1'],'POST','/v1/monitors/1/check'),(['monitor','delete','1'],'DELETE','/v1/monitors/1'),(['monitor','events','1'],'GET','/v1/monitors/1/events?limit=50&offset=0')]:
        assert main(args+tail,request=req)==0
        assert seen[-1][0:2]==(method,path)


def test_lifecycle_cloud_skips_ollama_and_refuses_unowned_stop(tmp_path,monkeypatch):
    settings=load_settings(root=tmp_path,overrides={'credential_env_file':None})
    monkeypatch.setattr(lifecycle,'_runtime_info',lambda s:None)
    status=lifecycle.status(settings)
    assert status['llm']['llm_provider'] == 'none' and status['llm']['inference_verified'] is False
    state={'api':{'pid':123,'creation':'old','executable':'python.exe'},'ollama':{'pid':321,'creation':'old','executable':'ollama.exe'}}
    lifecycle._save_state(settings,state)
    monkeypatch.setattr(lifecycle,'_identity',lambda pid:{'pid':pid,'creation':'new','executable':'other.exe'})
    killed=[]
    monkeypatch.setattr(lifecycle,'_terminate_owned',lambda rec:killed.append(rec))
    result=lifecycle.stop(settings)
    assert killed==[] and result['stopped']==[]


@pytest.mark.skipif(os.name != 'nt', reason='Real Windows venv launcher regression')
def test_windows_venv_child_owned_pid_handshake(tmp_path):
    settings=load_settings(root=tmp_path,overrides={'credential_env_file':None})
    # Use the real project venv redirector, not mocks or the base interpreter.
    python=Path(sys.executable)
    env=dict(os.environ)
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
    env['LOCAL_SCRAPE_INSTANCE_ID']='regression-instance'
    output=tmp_path/'actual.json'
    code=("import os,json,time; from pathlib import Path; "
          "from local_scrape import lifecycle; "
          "from local_scrape.settings import load_settings; "
          f"s=load_settings(root={str(tmp_path)!r},overrides={{'credential_env_file':None}}); "
          "getattr(lifecycle,'_publish_ownership',lambda s:None)(s); "
          f"Path({str(output)!r}).write_text(json.dumps({{'pid':os.getpid(),'parent':os.getppid()}})); "
          "time.sleep(30)")
    record=None
    try:
        record=lifecycle._spawn(settings,[str(python),'-c',code],'api',env)
        import time
        until=time.monotonic()+5
        while not output.exists() and time.monotonic()<until:
            time.sleep(.05)
        actual=json.loads(output.read_text())
        assert record['pid']==actual['pid']
        assert record['pid'] != actual['parent'], 'Must record interpreter, not venv launcher'
        assert lifecycle._owned(record)
        assert record['instance_id']=='regression-instance'
        # A stale creation stamp must never terminate even the genuine process.
        stale={**record,'creation':'0'}
        assert lifecycle._terminate_owned(stale) is False
        assert lifecycle._owned(record)
        assert lifecycle._terminate_owned(record) is True
        assert not lifecycle._owned(record)
    finally:
        if record and lifecycle._owned(record):
            lifecycle._terminate_owned(record)


def test_named_cloud_credential_only(tmp_path,monkeypatch):
    envfile=tmp_path/'credentials.env'
    envfile.write_text('OTHER_KEY=do-not-load\nexport OLLAMA_API_KEY="test-only-key" # comment\n',encoding='utf-8')
    monkeypatch.delenv('OLLAMA_API_KEY',raising=False)
    settings=load_settings(root=tmp_path,overrides={'credential_env_file':str(envfile), 'ollama_mode': 'cloud'})
    assert settings['ollama_api_key']=='test-only-key'
    assert 'OTHER_KEY' not in settings
    monkeypatch.setenv('OLLAMA_API_KEY','environment-precedence')
    assert load_settings(root=tmp_path,overrides={'credential_env_file':str(envfile), 'ollama_mode': 'cloud'})['ollama_api_key']=='environment-precedence'
