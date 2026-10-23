"""Loopback-only, token-authenticated REST service. Engine imports are lazy."""
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import logging
from pathlib import Path
from typing import Literal
from fastapi import Depends, FastAPI, Query, Request
from fastapi.security import HTTPBearer
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.exceptions import HTTPException
from .settings import load_settings, token_for
from .store import Store
from .workflows import Workflows, WorkflowError, error_payload


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, populate_by_name=False)


class ScrapeRequest(StrictRequest):
    url: str = Field(min_length=1,max_length=4096)
    render: Literal['auto','fast','js'] = 'auto'
    formats: list[Literal['text','markdown','html','links','images','metadata','screenshot']] | None = Field(default=None,max_length=7)
    timeout_seconds: int | None = Field(default=None,ge=1,le=120)


class ExtractRequest(StrictRequest):
    prompt: str = Field(min_length=1,max_length=10000)
    schema_: dict | None = Field(default=None,alias='schema')
    url: str | None = Field(default=None,max_length=4096)
    text: str | None = Field(default=None,max_length=100000)
    html: str | None = Field(default=None,max_length=100000)
    render: Literal['auto','fast','js'] = 'auto'

    @model_validator(mode='after')
    def one_source(self):
        if sum(v is not None for v in (self.url,self.text,self.html)) != 1:
            raise ValueError('Exactly one of url, text or html is required')
        return self


class SchemaRequest(StrictRequest):
    prompt: str = Field(min_length=1,max_length=10000)


class CrawlRequest(StrictRequest):
    url: str = Field(min_length=1,max_length=4096)
    render: Literal['auto','fast','js'] = 'auto'
    max_pages: int | None = Field(default=None,ge=1,le=100)
    max_depth: int | None = Field(default=None,ge=0,le=10)
    deadline_seconds: int | None = Field(default=None,ge=1,le=600)


class SearchRequest(StrictRequest):
    query: str = Field(min_length=1,max_length=500)
    max_results: int | None = Field(default=None,ge=1,le=20)


class MonitorRequest(StrictRequest):
    url: str = Field(min_length=1,max_length=4096)
    interval_seconds: int = Field(default=3600,ge=60,le=2592000)
    render: Literal['auto','fast','js'] = 'auto'


def failure(code,message,status=400,details=None):
    return JSONResponse(status_code=status,content={'ok':False,'error':{'code':code,'message':message,'details':details or {}}})


def create_app(settings=None, *, engine_factory=None, search_provider=None, run_scheduler=True):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or load_settings()
        app.state.settings = config
        app.state.token = token_for(config)
        if engine_factory is None:
            from .core import Engine
            factory = Engine
        else:
            factory = engine_factory
        engine = factory(config)
        app.state.engine = engine
        app.state.store = Store(Path(config['data_dir'])/'state.sqlite3',config['history_limit'],config['event_limit'])
        app.state.workflows = Workflows(engine,app.state.store,config,search_provider)
        app.state.operation_lock = asyncio.Semaphore(1)
        task = asyncio.create_task(app.state.workflows.scheduler()) if run_scheduler else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await engine.close()

    app = FastAPI(title='LocalScrape',version='0.1.0',lifespan=lifespan,
                  dependencies=[Depends(HTTPBearer(auto_error=False))])

    @app.middleware('http')
    async def auth_and_limit(request: Request, call_next):
        public = request.url.path in {'/health','/docs','/redoc','/openapi.json','/docs/oauth2-redirect'} or request.url.path.startswith('/static/')
        if not public:
            supplied = request.headers.get('authorization','')
            expected = 'Bearer '+getattr(app.state,'token','')
            if not hmac.compare_digest(supplied.encode(),expected.encode()) or expected == 'Bearer ':
                return failure('UNAUTHORIZED','A local Bearer API token is required',401)
        # Chunked requests must respect the same cap as Content-Length requests.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1000000:
                return failure('REQUEST_TOO_LARGE','Request body exceeds 1000000 bytes',413)
        request._body = bytes(body)
        try:
            return await call_next(request)
        except Exception as exc:
            logging.getLogger('local_scrape').error('Unhandled API failure type=%s',type(exc).__name__)
            return failure('INTERNAL_ERROR','Operation failed; see local service diagnostics',500)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # Do not echo arbitrary input or Pydantic exception context.
        fields = [{'field':list(e['loc']),'type':e['type']} for e in exc.errors()]
        return failure('VALIDATION_ERROR','Request validation failed',422,{'fields':fields})

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        return failure('HTTP_ERROR',str(exc.detail),exc.status_code)

    async def operation(name,payload,call):
        try:
            async with app.state.operation_lock:
                result = await call()
            response = {'ok':True,'data':result}
            app.state.store.record_history(name,payload,response)
            return response
        except Exception as exc:
            logging.getLogger('local_scrape').warning('Operation failed operation=%s type=%s',name,type(exc).__name__)
            err = error_payload(exc)
            response = {'ok':False,'error':err}
            app.state.store.record_history(name,payload,response)
            status = getattr(exc,'status_code',504 if isinstance(exc,TimeoutError) else 500)
            return JSONResponse(status_code=status if 400 <= status <= 599 else 500,content=response)

    @app.get('/health',openapi_extra={'security':[]})
    async def health():
        return {'ok':True,'data':{'status':'running'}}

    @app.get('/v1/health')
    async def detailed_health():
        async def call():
            result = await app.state.engine.health()
            return {k:v for k,v in result.items() if k in {'status','available','model','ollama_mode','ollama_available','browser_available'}}
        return await operation('health',{},call)

    @app.get('/v1/runtime')
    async def runtime_info():
        import os
        return {'ok':True,'data':{'pid':os.getpid(),'instance_id':os.environ.get('LOCAL_SCRAPE_INSTANCE_ID','')}}

    @app.post('/v1/runtime/stop')
    async def runtime_stop():
        callback = getattr(app.state,'request_shutdown',None)
        if callback is None:
            return failure('NOT_MANAGED','This API was not started by the lifecycle manager',409)
        callback()
        return {'ok':True,'data':{'stopping':True}}

    @app.post('/v1/scrape')
    async def scrape(payload: ScrapeRequest):
        args = payload.model_dump(exclude_none=True)
        if payload.timeout_seconds and payload.timeout_seconds > app.state.settings['timeout_seconds']:
            return failure('INVALID_ARGUMENT','timeout_seconds exceeds configured maximum',422)
        return await operation('scrape',args,lambda:app.state.engine.scrape(**args))

    @app.post('/v1/extract')
    async def extract(payload: ExtractRequest):
        args = payload.model_dump(by_alias=True,exclude_none=True)
        return await operation('extract',args,lambda:app.state.engine.extract(**args))

    @app.post('/v1/schema')
    async def schema(payload: SchemaRequest):
        return await operation('schema',{},lambda:app.state.engine.schema(payload.prompt))

    @app.post('/v1/crawl')
    async def crawl(payload: CrawlRequest):
        args = payload.model_dump(exclude_none=True)
        return await operation('crawl',args,lambda:app.state.workflows.crawl(**args))

    @app.post('/v1/search')
    async def search(payload: SearchRequest):
        args = payload.model_dump(exclude_none=True)
        return await operation('search',args,lambda:app.state.workflows.search(**args))

    @app.get('/v1/history')
    async def history(limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0,le=100000)):
        return {'ok':True,'data':app.state.store.history(limit,offset)}

    @app.post('/v1/monitors')
    async def monitor_create(payload: MonitorRequest):
        args=payload.model_dump()
        return await operation('monitor_create',args,lambda:app.state.workflows.create_monitor(**args))

    @app.get('/v1/monitors')
    async def monitor_list(limit:int=Query(default=100,ge=1,le=100),offset:int=Query(default=0,ge=0,le=100000)):
        return {'ok':True,'data':app.state.store.monitors(limit,offset)}

    @app.get('/v1/monitors/{ident}')
    async def monitor_get(ident:int):
        item=app.state.store.get_monitor(ident)
        if item is None:
            return failure('NOT_FOUND','Monitor not found',404)
        item.pop('last_text',None)
        return {'ok':True,'data':item}

    @app.delete('/v1/monitors/{ident}')
    async def monitor_delete(ident:int):
        if not app.state.store.delete_monitor(ident):
            return failure('NOT_FOUND','Monitor not found',404)
        return {'ok':True,'data':{'id':ident,'deleted':True}}

    @app.post('/v1/monitors/{ident}/check')
    async def monitor_check(ident:int):
        return await operation('monitor_check',{'monitor_id':ident},lambda:app.state.workflows.check_monitor(ident))

    @app.get('/v1/monitors/{ident}/events')
    async def monitor_events(ident:int,limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0,le=100000)):
        if not app.state.store.get_monitor(ident):
            return failure('NOT_FOUND','Monitor not found',404)
        return {'ok':True,'data':app.state.store.events(ident,limit,offset)}

    return app


def serve():
    import os
    import uvicorn
    settings=load_settings()
    from .lifecycle import _publish_ownership
    _publish_ownership(settings)
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path(settings['root'])/'browsers')
    app=create_app(settings)
    server=uvicorn.Server(uvicorn.Config(app,host=settings['api_host'],port=settings['api_port'],access_log=False))
    app.state.request_shutdown=lambda: setattr(server,'should_exit',True)
    server.run()


if __name__ == '__main__':
    serve()
