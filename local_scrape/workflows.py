"""Bounded workflows around the shared asynchronous Engine contract."""
import asyncio
from collections import deque
import difflib
import hashlib
import ipaddress
import time
from urllib.parse import urljoin, urlsplit, urlunsplit


class WorkflowError(Exception):
    def __init__(self, code, message, status_code=400, details=None):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code
        self.details = details or {}


def error_payload(exc):
    if hasattr(exc, 'code') and hasattr(exc, 'message'):
        return {'code': str(exc.code), 'message': str(exc.message), 'details': getattr(exc, 'details', {}) or {}}
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return {'code': 'TIMEOUT', 'message': 'Operation exceeded its deadline', 'details': {}}
    return {'code': 'INTERNAL_ERROR', 'message': 'Operation failed; see local service diagnostics', 'details': {}}


def bounded(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise WorkflowError('INVALID_ARGUMENT', f'{name} must be an integer between {low} and {high}', 422)
    return value


def canonical_url(url, allow_private=False):
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password or len(url) > 4096:
            raise ValueError()
        host = parts.hostname.lower().rstrip('.')
        port = parts.port
        if not allow_private:
            if host in ('localhost', 'metadata.google.internal') or host.endswith(('.localhost', '.local', '.internal')):
                raise ValueError()
            try:
                addr = ipaddress.ip_address(host)
            except ValueError:
                addr = None
            if addr and not addr.is_global:
                raise ValueError()
        # Full DNS/redirect/browser URL enforcement is performed by Engine on fetch.
        if ':' in host:
            host = '[' + host + ']'
        default = 443 if parts.scheme.lower() == 'https' else 80
        authority = host + (f':{port}' if port and port != default else '')
        return urlunsplit((parts.scheme.lower(), authority, parts.path or '/', parts.query, ''))
    except (TypeError, ValueError):
        raise WorkflowError('INVALID_URL', 'A permitted HTTP(S) URL without credentials is required', 422) from None


def origin(url):
    u = urlsplit(url)
    return u.scheme, u.hostname, u.port or (443 if u.scheme == 'https' else 80)


def public_search(query, limit):
    """Only free public backends. Never manufacture fallback results."""
    from ddgs import DDGS
    errors = []
    for backend in ('duckduckgo', 'bing', 'brave'):
        try:
            rows = DDGS(timeout=8).text(query, max_results=limit, backend=backend)
            if rows:
                return {'rows': rows, 'backend': backend, 'warnings': errors}
            errors.append(f'{backend}: no results')
        except Exception as exc:
            # Provider exception messages can contain URLs/credentials: expose only type.
            errors.append(f'{backend}: {type(exc).__name__}')
    raise WorkflowError('SEARCH_UNAVAILABLE', 'Public search providers returned no results or were blocked', 502, {'providers': errors})


class Workflows:
    def __init__(self, engine, store, settings, search_provider=None):
        self.engine, self.store, self.settings = engine, store, settings
        self.search_provider = search_provider or public_search
        self.monitor_lock = asyncio.Lock()
        self.search_lock = asyncio.Lock()

    async def crawl(self, url, *, max_pages=None, max_depth=None, deadline_seconds=None, render='auto'):
        max_pages = bounded(max_pages if max_pages is not None else self.settings['max_pages'], 1, self.settings['max_pages'], 'max_pages')
        max_depth = bounded(max_depth if max_depth is not None else self.settings['max_depth'], 0, self.settings['max_depth'], 'max_depth')
        deadline = bounded(deadline_seconds if deadline_seconds is not None else self.settings['crawl_deadline_seconds'], 1, self.settings['crawl_deadline_seconds'], 'deadline_seconds')
        if render not in ('auto','fast','js'):
            raise WorkflowError('INVALID_ARGUMENT','Unsupported render mode',422)
        start = canonical_url(url, self.settings['allow_private_urls'])
        site = origin(start)
        queue, seen = deque([(start,0)]), {start}
        collected = set()
        pages, errors, warnings = [], [], []
        attempted = 0
        until = time.monotonic() + deadline
        while queue and attempted < max_pages:
            remaining = until - time.monotonic()
            if remaining <= 0:
                warnings.append('deadline_reached')
                break
            current, depth = queue.popleft()
            attempted += 1
            try:
                result = await asyncio.wait_for(self.engine.scrape(current, render=render), timeout=remaining)
                final = canonical_url(result.get('final_url',current), self.settings['allow_private_urls'])
                if origin(final) != site:
                    raise WorkflowError('CROSS_ORIGIN_REDIRECT','Crawl page redirected to a different origin',400)
                seen.add(final)
                # Queued URLs are not necessarily collected documents: a new
                # redirect target may already be queued, or an alias may repeat one.
                if final in collected:
                    continue
                collected.add(final)
                pages.append({'depth':depth, **result})
                if depth < max_depth:
                    for link in result.get('links',[]):
                        try:
                            target = canonical_url(urljoin(final, link['url']), self.settings['allow_private_urls'])
                        except (WorkflowError, KeyError, TypeError):
                            continue
                        if origin(target) == site and target not in seen:
                            seen.add(target)
                            if len(queue) < max_pages:
                                queue.append((target,depth+1))
                elif result.get('links'):
                    if 'depth_limit_reached' not in warnings:
                        warnings.append('depth_limit_reached')
            except Exception as exc:
                errors.append({'url':current,'error':error_payload(exc)})
                if isinstance(exc, TimeoutError):
                    warnings.append('deadline_reached')
                    break
        if queue and attempted >= max_pages:
            warnings.append('page_limit_reached')
        if not pages:
            raise WorkflowError('CRAWL_FAILED','No crawl page was fetched successfully',502, {'errors':errors,'warnings':warnings})
        return dict(url=start,pages=pages,errors=errors,attempted=attempted,partial=bool(errors or warnings),warnings=warnings)

    async def search(self, query, *, max_results=None):
        if not isinstance(query,str) or not query.strip() or len(query) > 500:
            raise WorkflowError('INVALID_ARGUMENT','query must contain 1 to 500 characters',422)
        limit = bounded(max_results if max_results is not None else self.settings['search_max_results'],1,self.settings['search_max_results'],'max_results')
        async with self.search_lock:
            try:
                raw = await asyncio.wait_for(asyncio.to_thread(self.search_provider,query,limit), timeout=min(self.settings['timeout_seconds'],45))
            except WorkflowError:
                raise
            except Exception as exc:
                raise WorkflowError('SEARCH_UNAVAILABLE','Public search failed or timed out',502,{'reason':type(exc).__name__}) from None
        rows = raw.get('rows',[]) if isinstance(raw,dict) else raw
        results, seen = [], set()
        for row in rows:
            try:
                url = canonical_url(row.get('href') or row.get('url'),False)
            except WorkflowError:
                continue
            if url not in seen:
                results.append(dict(url=url,title=str(row.get('title',''))[:1000],snippet=str(row.get('body',''))[:3000]))
                seen.add(url)
            if len(results) >= limit:
                break
        if not results:
            raise WorkflowError('SEARCH_UNAVAILABLE','Public search returned no results',502)
        return dict(query=query,results=results,backend=raw.get('backend','injected') if isinstance(raw,dict) else 'injected',warnings=raw.get('warnings',[]) if isinstance(raw,dict) else [])

    async def create_monitor(self, url, interval_seconds=3600, render='auto'):
        url = canonical_url(url,self.settings['allow_private_urls'])
        bounded(interval_seconds,60,2592000,'interval_seconds')
        if render not in ('auto','fast','js'):
            raise WorkflowError('INVALID_ARGUMENT','Unsupported render mode',422)
        async with self.monitor_lock:
            if self.store.monitors(1)['total'] >= self.settings['max_monitors']:
                raise WorkflowError('MONITOR_LIMIT','Configured monitor count limit reached',409)
            return self.store.create_monitor(url,interval_seconds,render)

    async def check_monitor(self, ident):
        async with self.monitor_lock:
            monitor = self.store.get_monitor(ident)
            if monitor is None:
                raise WorkflowError('NOT_FOUND','Monitor not found',404)
            try:
                result = await self.engine.scrape(monitor['url'],render=monitor['render'])
                if result.get('truncated'):
                    raise WorkflowError('MONITOR_PARTIAL','Cannot reliably compare a truncated document',502)
                text = result.get('text','')
                if not text.strip():
                    raise WorkflowError('MONITOR_EMPTY','Monitor returned empty text',502)
                # Bounded stored snapshots; never silently compare partial documents.
                if len(text) > self.settings['max_response_bytes']:
                    raise WorkflowError('MONITOR_PARTIAL','Monitor text exceeds snapshot budget',502)
                digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
                kind = 'first' if monitor['last_hash'] is None else ('unchanged' if digest == monitor['last_hash'] else 'changed')
                diff = ''
                if kind == 'changed':
                    lines = difflib.unified_diff((monitor['last_text'] or '').splitlines(),text.splitlines(),fromfile='before',tofile='after',lineterm='')
                    diff = '\n'.join(lines)[:20000]
                return self.store.save_check(ident,kind,digest,text,diff,None)
            except Exception as exc:
                err = error_payload(exc)
                self.store.save_check(ident,'error',None,None,'',err['code']+': '+err['message'])
                raise

    async def scheduler(self):
        while True:
            for ident in self.store.due():
                try:
                    await self.check_monitor(ident)
                except Exception:
                    # Error event and next check are persisted; one failed target cannot stop scheduling.
                    pass
            await asyncio.sleep(self.settings['monitor_poll_seconds'])
