"""Machine-readable CLI: one JSON object on stdout, nonzero on failure."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from .settings import load_settings, token_for
from .workflows import WorkflowError, error_payload


class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise WorkflowError('INVALID_ARGUMENT',message,422)


def parser():
    p=Parser(prog='local-scrape',description='Self-hosted scraping with optional Ollama Cloud AI. Outputs JSON by default.')
    def common(q):
        q.add_argument('--root',default=argparse.SUPPRESS,help='Installation root (default: package installation)')
        q.add_argument('--json',action='store_true',default=argparse.SUPPRESS,help='Machine JSON (also the default)')
    common(p)
    subs=p.add_subparsers(dest='command',required=True,parser_class=Parser)
    def command(name,help):
        q=subs.add_parser(name,help=help); common(q); return q
    def render(q): q.add_argument('--render',choices=['auto','fast','js'],default='auto')
    def pagination(q):
        q.add_argument('--limit',type=int,default=50)
        q.add_argument('--offset',type=int,default=0)
    command('health','Authenticated engine readiness diagnostics')
    command('start','Start project API; local Ollama only in explicit local mode')
    command('status','Inspect API and exactly-owned process state')
    command('stop','Stop only exactly-owned project processes')
    q=command('scrape','Fetch a public URL'); q.add_argument('url'); render(q)
    q.add_argument('--formats',nargs='+',choices=['text','markdown','html','links','images','metadata','screenshot'])
    q.add_argument('--timeout-seconds',type=int)
    q=command('extract','AI extraction from exactly one URL/text/HTML source')
    q.add_argument('--prompt',required=True)
    group=q.add_mutually_exclusive_group(required=True)
    for name in ['url','text','html','text-file','html-file']:
        group.add_argument('--'+name)
    q.add_argument('--schema-file',help='JSON Schema file'); render(q)
    q=command('schema','Generate a JSON Schema with AI'); q.add_argument('--prompt',required=True)
    q=command('crawl','Bounded same-origin crawl'); q.add_argument('url'); render(q)
    q.add_argument('--max-pages',type=int); q.add_argument('--max-depth',type=int); q.add_argument('--deadline-seconds',type=int)
    q=command('search','Search free public search engines'); q.add_argument('query'); q.add_argument('--max-results',type=int)
    q=command('history','Persisted operation history (redacted summaries)'); pagination(q)
    q=command('monitor','Create/list/get/delete/check monitors or read events')
    ms=q.add_subparsers(dest='action',required=True,parser_class=Parser)
    for action in ['create','list','get','delete','check','events']:
        m=ms.add_parser(action); common(m)
        if action=='create':
            m.add_argument('url'); m.add_argument('--interval-seconds',type=int,default=3600); render(m)
        if action in ['get','delete','check','events']: m.add_argument('id',type=int)
        if action in ['list','events']: pagination(m)
    return p


def api_request(settings,method,path,payload=None):
    import httpx
    maximum=settings['crawl_deadline_seconds'] if '/crawl' in path else (settings['ollama_timeout_seconds']*3+settings['timeout_seconds']+15)
    try:
        with httpx.Client(trust_env=False,timeout=maximum) as client:
            r=client.request(method,f"http://127.0.0.1:{settings['api_port']}"+path,headers={'Authorization':'Bearer '+token_for(settings)},json=payload if payload is not None else None)
        result=r.json()
        if not isinstance(result,dict) or type(result.get('ok')) is not bool or ('data' not in result if result.get('ok') else 'error' not in result):
            raise ValueError()
        if r.status_code>=400 and result['ok']:
            raise ValueError()
        return result
    except httpx.TimeoutException:
        raise WorkflowError('API_TIMEOUT','API request exceeded its deadline',504) from None
    except httpx.HTTPError:
        raise WorkflowError('API_UNAVAILABLE','Cannot reach local API; run local-scrape start',503) from None
    except ValueError:
        raise WorkflowError('INVALID_API_RESPONSE','Local listener returned an invalid API response',502) from None


def read_input(filename,limit):
    path=Path(filename)
    if path.stat().st_size > limit*4:
        raise WorkflowError('INPUT_TOO_LARGE','Input file exceeds its size limit',422)
    text=path.read_text(encoding='utf-8-sig')
    if len(text)>limit:
        raise WorkflowError('INPUT_TOO_LARGE','Input file exceeds its character limit',422)
    return text


def main(argv=None,*,request=None):
    try:
        args=vars(parser().parse_args(argv)); settings=load_settings(root=args.pop('root',None))
        args.pop('json',None); cmd=args.pop('command'); request=request or api_request
        if cmd in ('start','status','stop'):
            from . import lifecycle
            result={'ok':True,'data':getattr(lifecycle,cmd)(settings)}
        else:
            method,path,payload='POST','/v1/'+cmd,args
            if cmd=='health': method,path,payload='GET','/v1/health',None
            elif cmd=='history': method,path,payload='GET','/v1/history?'+urlencode(args),None
            elif cmd=='extract':
                for key in ('text','html'):
                    filename=args.pop(key+'_file')
                    if filename is not None: args[key]=read_input(filename,100000)
                schema_file=args.pop('schema_file')
                if schema_file:
                    args['schema']=json.loads(read_input(schema_file,100000))
                    if not isinstance(args['schema'],dict):
                        raise WorkflowError('INVALID_ARGUMENT','Schema file must contain a JSON object',422)
            elif cmd=='monitor':
                action=args.pop('action'); path='/v1/monitors'
                if action in ('get','delete','check','events'):
                    ident=args.pop('id')
                    if ident < 1: raise WorkflowError('INVALID_ARGUMENT','Monitor id must be positive',422)
                    path+=f'/{ident}'
                if action=='check': path+='/check'
                if action=='events': path+='/events'
                if action=='delete': method='DELETE'; payload=None
                elif action in ('get','list','events'):
                    method='GET'; payload=None
                    if args: path+='?'+urlencode(args)
            if payload is not None:
                payload={k:v for k,v in payload.items() if v is not None}
            result=request(settings,method,path,payload)
        print(json.dumps(result,ensure_ascii=False,separators=(',',':')))
        return 0 if result['ok'] else 1
    except WorkflowError as exc:
        print(json.dumps({'ok':False,'error':error_payload(exc)},ensure_ascii=False))
        return 2 if exc.code=='INVALID_ARGUMENT' else 1
    except (ValueError,OSError) as exc:
        # Paths, config contents and secrets are not echoed.
        print(json.dumps({'ok':False,'error':{'code':'CONFIG_OR_INPUT_ERROR','message':'Invalid or unreadable configuration/input file','details':{'reason':type(exc).__name__}}}))
        return 1
    except Exception:
        print(json.dumps({'ok':False,'error':{'code':'INTERNAL_ERROR','message':'Command failed without exposing internal details','details':{}}}))
        return 1
