"""Bounded HTTP/browser/PDF scraping and validated model extraction."""
import asyncio
from contextlib import suppress
import os
from pathlib import Path
import uuid
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
import httpx
from .security import URLPolicy, ScrapeError
from .parsing import parse_html, parse_pdf, normalize_text
from .extraction import Extractor, check_schema


class Engine:
    def __init__(self, settings):
        self.settings = settings
        self.policy = URLPolicy(settings.get('allow_private_urls', False))
        self.client = httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                      limits=httpx.Limits(max_connections=6, max_keepalive_connections=0))
        self.extractor = Extractor(settings)
        self.browser_lock = asyncio.Lock()

    async def close(self):
        await self.client.aclose()
        await self.extractor.close()

    async def _once(self, url, limit=None):
        target = await self.policy.resolve(url)
        limit = limit or self.settings.get('max_response_bytes',5000000)
        try:
            async with self.client.stream('GET', target.pinned_url,
                    headers={'Host':target.host_header, 'User-Agent':'LocalScrape/0.1', 'Accept-Encoding':'identity'},
                    extensions={'sni_hostname':target.hostname},
                    timeout=self.settings.get('timeout_seconds',45)) as response:
                if response.headers.get('content-length','').isdigit() and int(response.headers['content-length']) > limit:
                    raise ScrapeError('RESPONSE_TOO_LARGE', 'Response exceeds the configured byte limit', 413)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise ScrapeError('RESPONSE_TOO_LARGE', 'Response exceeds the configured byte limit', 413)
                return dict(url=target.url, status=response.status_code, headers=dict(response.headers), body=bytes(body))
        except httpx.TimeoutException:
            raise ScrapeError('TIMEOUT', 'Website request exceeded its deadline', 504) from None
        except httpx.HTTPError:
            raise ScrapeError('FETCH_FAILED', 'Website request failed', 502) from None

    async def _fetch(self, url, limit=None, robots=False):
        for _ in range(6):
            if robots:
                await self._robots(url)
            result = await self._once(url, limit)
            if result['status'] in (301,302,303,307,308):
                location = result['headers'].get('location')
                if not location:
                    raise ScrapeError('INVALID_REDIRECT', 'Redirect has no destination', 502)
                url = urljoin(result['url'],location)
                continue
            return result
        raise ScrapeError('REDIRECT_LIMIT', 'Website exceeded the redirect limit', 502)

    async def _robots(self, url):
        if not self.settings.get('respect_robots',True):
            return
        parts = urlsplit(url)
        robots_url = urlunsplit((parts.scheme,parts.netloc,'/robots.txt','',''))
        result = await self._fetch(robots_url,limit=256000)
        if result['status'] in (401,403):
            raise ScrapeError('ROBOTS_DENIED', 'Website robots policy denies access', 403)
        if result['status'] in (404,410):
            return
        if not 200 <= result['status'] < 300:
            raise ScrapeError('ROBOTS_UNAVAILABLE', 'Website robots policy could not be checked', 502)
        parser = RobotFileParser()
        parser.parse(result['body'].decode('utf-8',errors='replace').splitlines())
        if not parser.can_fetch('LocalScrape',url):
            raise ScrapeError('ROBOTS_DENIED', 'Website robots policy denies this path', 403)

    @staticmethod
    def _status(result):
        if not 200 <= result['status'] < 300:
            raise ScrapeError('HTTP_STATUS', 'Website returned a non-success status', 502, {'upstream_status':result['status']})

    @staticmethod
    def _decode(result):
        # HTTPX decodes declared charset, with a safe UTF-8 replacement fallback.
        return httpx.Response(200, headers=result['headers'], content=result['body']).text

    async def _browser(self, url, screenshot=False):
        from playwright.async_api import async_playwright, Error as BrowserError
        root = Path(self.settings.get('root',Path(__file__).resolve().parent.parent))
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(root/'browsers'))
        limit = self.settings.get('max_response_bytes',5000000)
        errors, warnings, total = [], [], 0
        byte_lock = asyncio.Lock()
        budget_error = None
        async with self.browser_lock:
            try:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(headless=True, args=['--disable-quic','--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
                    try:
                        context = await browser.new_context(service_workers='block', accept_downloads=False)
                        async def route_handler(route):
                            nonlocal total, budget_error
                            request = route.request
                            if request.method != 'GET':
                                await route.abort()
                                return
                            try:
                                if request.is_navigation_request():
                                    await self._robots(request.url)
                                # Hold the allowance across the fetch await: concurrent
                                # routes must not spend the same remaining bytes.
                                async with byte_lock:
                                    if budget_error is not None:
                                        raise budget_error
                                    remaining = limit - total
                                    if remaining <= 0:
                                        raise ScrapeError('RESPONSE_TOO_LARGE', 'Browser responses exceed the aggregate byte limit', 413)
                                    response = await self._once(request.url, limit=remaining)
                                    total += len(response['body'])
                                headers = {k:v for k,v in response['headers'].items() if k.lower() not in ('content-encoding','content-length','transfer-encoding','set-cookie')}
                                await route.fulfill(status=response['status'],headers=headers,body=response['body'])
                            except ScrapeError as exc:
                                if exc.code == 'RESPONSE_TOO_LARGE':
                                    budget_error = exc
                                    errors.append(exc)
                                elif request.is_navigation_request():
                                    errors.append(exc)
                                elif 'blocked_subresource' not in warnings:
                                    warnings.append('blocked_subresource')
                                await route.abort()
                        await context.route('**/*',route_handler)
                        await context.route_web_socket('**/*',lambda ws: ws.close())
                        page = await context.new_page()
                        response = await page.goto(url,wait_until='domcontentloaded',timeout=self.settings.get('timeout_seconds',45)*1000)
                        with suppress(BrowserError):
                            await page.wait_for_load_state('networkidle',timeout=1500)
                        if errors:
                            raise errors[0]
                        if response is None:
                            raise ScrapeError('BROWSER_EMPTY', 'Browser returned no document', 502)
                        self._status({'status':response.status})
                        html = await page.content()
                        if len(html.encode('utf-8')) > limit:
                            raise ScrapeError('RESPONSE_TOO_LARGE','Rendered HTML exceeds its byte limit',413)
                        result = dict(url=page.url,status=response.status,headers={'content-type':'text/html'},body=html.encode('utf-8'),warnings=warnings)
                        if screenshot:
                            folder = Path(self.settings.get('artifact_dir',root/'data/artifacts'))
                            folder.mkdir(parents=True,exist_ok=True)
                            path = folder/(uuid.uuid4().hex+'.png')
                            # Viewport screenshot bounds image dimensions and memory.
                            await page.screenshot(path=str(path),full_page=False)
                            result['screenshot'] = path.relative_to(root).as_posix()
                        return result
                    finally:
                        await browser.close()
            except BrowserError:
                if errors:
                    raise errors[0]
                raise ScrapeError('BROWSER_UNAVAILABLE','Headless browser failed to launch or load this page',503) from None

    async def scrape(self,url,*,render='auto',formats=None,timeout_seconds=None):
        formats = ['markdown','metadata'] if formats is None else formats
        supported = {'text','markdown','html','links','images','metadata','screenshot'}
        if render not in ('auto','fast','js') or not isinstance(formats,list) or any(x not in supported for x in formats):
            raise ScrapeError('INVALID_ARGUMENT','Unsupported render mode or output format',422)
        maximum = self.settings.get('timeout_seconds',45)
        timeout = maximum if timeout_seconds is None else timeout_seconds
        if type(timeout) is not int or not 1 <= timeout <= maximum:
            raise ScrapeError('INVALID_ARGUMENT','timeout_seconds exceeds the configured bound',422)
        try:
            async with asyncio.timeout(timeout):
                target = await self.policy.resolve(url)
                initial = target.url
                if render == 'js' or 'screenshot' in formats:
                    result = await self._browser(initial,'screenshot' in formats)
                else:
                    result = await self._fetch(initial,robots=True)
                    self._status(result)
                kind = result['headers'].get('content-type','').split(';')[0].lower()
                if kind == 'application/pdf' or result['body'].startswith(b'%PDF-'):
                    parsed = await asyncio.to_thread(parse_pdf,result['body'],self.settings.get('max_response_bytes',5000000))
                elif kind in ('text/html','application/xhtml+xml',''):
                    html = self._decode(result)
                    parsed = parse_html(html,result['url'])
                    if render == 'auto' and 'screenshot' not in formats and len(parsed['text']) < 80 and '<script' in html.lower():
                        result = await self._browser(initial)
                        html = self._decode(result)
                        parsed = parse_html(html,result['url'])
                    parsed['html'] = html
                elif kind.startswith('text/') or kind in ('application/json','application/xml'):
                    text = normalize_text(self._decode(result))
                    parsed = dict(title='',text=text,markdown=text,links=[],images=[],metadata={})
                else:
                    raise ScrapeError('UNSUPPORTED_CONTENT','Content type is not a supported document',415)
                if not parsed['text'].strip():
                    raise ScrapeError('EMPTY_CONTENT','No extractable text was found; scanned PDFs require OCR',422)
                challenge = (parsed.get('title','')+'\n'+parsed['text'][:2000]).lower()
                if any(marker in challenge for marker in ('verify you are human','just a moment...','checking your browser','attention required! | cloudflare')):
                    raise ScrapeError('CHALLENGE_PAGE','Website returned an anti-bot challenge',502)
                output = dict(url=initial,final_url=result['url'],title=parsed['title'],text=parsed['text'],links=parsed['links'],status_code=result['status'],content_type=kind,truncated=parsed.get('truncated',False),warnings=parsed.get('warnings',[])+result.get('warnings',[]))
                for key in formats:
                    if key in parsed:
                        output[key] = parsed[key]
                if 'screenshot' in result:
                    output['screenshot'] = result['screenshot']
                return output
        except TimeoutError:
            raise ScrapeError('TIMEOUT','Scrape exceeded its total deadline',504) from None

    async def extract(self,prompt,*,schema=None,url=None,text=None,html=None,render='auto'):
        self._prompt(prompt)
        if sum(value is not None for value in (url,text,html)) != 1:
            raise ScrapeError('INVALID_ARGUMENT','Exactly one of url, text or html is required',422)
        warnings, truncated = [], False
        if url is not None:
            page = await self.scrape(url,render=render)
            content, source = page['text'], page['final_url']
            warnings, truncated = list(page['warnings']),page['truncated']
        elif html is not None:
            content, source = parse_html(html)['text'], 'html'
        else:
            content, source = text, 'text'
        if not isinstance(content,str) or not content.strip():
            raise ScrapeError('EMPTY_CONTENT','Extraction source is empty',422)
        budget = self.settings.get('max_input_chars',18000)
        if len(content) > budget:
            content, truncated = content[:budget],True
            warnings.append('input_truncated')
        schema = await self.schema(prompt) if schema is None else check_schema(schema)
        data = await self.extractor.extract(prompt,content,schema)
        return dict(data=data,schema=schema,source=source,model=self.settings.get('model','deepseek-v4.1-flash'),warnings=warnings,truncated=truncated)

    @staticmethod
    def _prompt(prompt):
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt) > 10000:
            raise ScrapeError('INVALID_ARGUMENT','Prompt must contain 1 to 10000 characters',422)

    async def schema(self,prompt):
        self._prompt(prompt)
        return await self.extractor.schema(prompt)

    async def health(self):
        # Launch the actual headless configuration; a full GUI executable is not required.
        browser_available = False
        try:
            from playwright.async_api import async_playwright
            root = Path(self.settings.get('root',Path(__file__).resolve().parent.parent))
            os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(root/'browsers'))
            async with asyncio.timeout(10):
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    browser_available = True
                    await browser.close()
        except Exception:
            pass
        available = False
        headers = {}
        key = self.settings.get('ollama_api_key')
        if self.extractor.mode == 'cloud' and key:
            headers['Authorization'] = 'Bearer '+key
        if self.extractor.mode == 'local' or key:
            try:
                async with asyncio.timeout(8):
                    async with self.extractor.client.stream('GET',self.extractor.url+'/api/tags',headers=headers,timeout=8) as response:
                        available = response.status_code == 200
            except (TimeoutError,httpx.HTTPError):
                pass
        return dict(status='ready' if available and browser_available else 'degraded',available=available,model=self.settings.get('model','deepseek-v4.1-flash'),ollama_mode=self.extractor.mode,ollama_available=available,browser_available=browser_available)
