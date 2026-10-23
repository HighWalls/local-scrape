import pytest
from fastapi.testclient import TestClient
from local_scrape.settings import load_settings, token_for
from local_scrape.service import create_app


class Engine:
    def __init__(self, settings): self.closed=False
    async def scrape(self,url,**kw): return {'url':url,'final_url':url,'text':'content','links':[], 'warnings':[], 'truncated':False}
    async def extract(self,prompt,**kw): return {'data':{'value':'ok'},'schema':kw['schema'],'warnings':[]}
    async def schema(self,prompt): return {'type':'object'}
    async def health(self): return {'status':'available','ollama_api_key':'MUST_NOT_LEAK'}
    async def close(self): self.closed=True


def test_public_docs_bearer_openapi_and_protected_operations(tmp_path):
    settings=load_settings(root=tmp_path,overrides={'credential_env_file':None})
    with TestClient(create_app(settings,engine_factory=Engine,run_scheduler=False)) as c:
        for path in ('/docs','/redoc','/openapi.json'):
            assert c.get(path).status_code==200
        schema=c.get('/openapi.json').json()
        assert schema['components']['securitySchemes']['HTTPBearer']['scheme']=='bearer'
        for path,operations in schema['paths'].items():
            if path.startswith('/v1/'):
                for operation in operations.values():
                    assert {'HTTPBearer':[]} in operation['security']
        for method,path in [('get','/v1/runtime'),('get','/v1/history'),('post','/v1/runtime/stop'),('get','/v1/monitors')]:
            assert getattr(c,method)(path).status_code==401
        c.headers['Authorization']='Bearer '+token_for(settings)
        assert c.get('/v1/runtime').status_code==200


def test_auth_validation_engine_and_history(tmp_path):
    settings=load_settings(root=tmp_path,overrides={'credential_env_file':None})
    app=create_app(settings,engine_factory=Engine)
    with TestClient(app) as c:
        assert c.get('/health').json()=={'ok':True,'data':{'status':'running'}}
        assert c.post('/v1/scrape',json={'url':'https://example.com'}).status_code==401
        c.headers['Authorization']='Bearer '+token_for(settings)
        assert c.post('/v1/scrape',json={'url':'https://example.com','allow_private_urls':True}).status_code==422
        assert c.post('/v1/scrape',json={'url':'https://example.com','timeout_seconds':999}).status_code==422
        r=c.post('/v1/scrape',json={'url':'https://example.com'})
        assert r.status_code==200 and r.json()['data']['text']=='content'
        assert c.get('/v1/history').json()['data']['items'][0]['operation']=='scrape'
        assert 'MUST_NOT_LEAK' not in c.get('/v1/health').text
        assert c.post('/v1/extract',json={'prompt':'get stuff','text':'a','html':'b'}).status_code==422
        assert c.post('/v1/schema',json={'prompt':'name'}).json()['data']=={'type':'object'}
        assert c.post('/v1/extract',json={'prompt':'name','text':'a','schema':{'type':'object'}}).json()['data']['data']=={'value':'ok'}
        m=c.post('/v1/monitors',json={'url':'https://example.com','interval_seconds':60}).json()['data']
        assert c.post(f"/v1/monitors/{m['id']}/check").json()['data']['kind']=='first'
        assert c.get(f"/v1/monitors/{m['id']}/events").json()['data']['total']==1
        assert c.get('/v1/monitors').json()['data']['total']==1
        assert c.delete(f"/v1/monitors/{m['id']}").json()['data']['deleted'] is True
        assert c.get(f"/v1/monitors/{m['id']}").status_code==404
    assert app.state.engine.closed


def test_error_envelopes_body_cap_and_unknown_route(tmp_path):
    settings=load_settings(root=tmp_path,overrides={'credential_env_file':None})
    with TestClient(create_app(settings,engine_factory=Engine)) as c:
        c.headers['Authorization']='Bearer '+token_for(settings)
        for method,path,kw in [('get','/unknown',{}),('post','/v1/schema',{'json':{}}),('get','/v1/history?limit=9999',{}),('post','/v1/schema',{'content':'x'*1000001})]:
            r=getattr(c,method)(path,**kw)
            assert r.status_code>=400 and r.json()['ok'] is False
            assert set(r.json()['error'])=={'code','message','details'}
