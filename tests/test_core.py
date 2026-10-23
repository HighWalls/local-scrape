import httpx
import pytest
from local_scrape.core import Engine, ScrapeError


@pytest.mark.asyncio
async def test_static_scrape_pins_connection_and_never_forwards_model_auth(tmp_path):
    seen = []
    async def serve(request):
        seen.append(request)
        assert 'authorization' not in request.headers
        return httpx.Response(200,headers={'content-type':'text/html'}, text='<title>Shop</title><p>Kettle</p><a href="/next">Next</a>')
    engine = Engine({'respect_robots':False,'ollama_api_key':'private-key','root':str(tmp_path),'artifact_dir':str(tmp_path/'artifacts')})
    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    result = await engine.scrape('https://93.184.216.34/', render='fast', formats=['markdown','metadata'])
    assert result['title'] == 'Shop' and result['status_code'] == 200
    assert result['links'] == [{'url':'https://93.184.216.34/next','text':'Next'}]
    assert result['truncated'] is False
    assert seen[0].headers['host'] == '93.184.216.34'
    assert seen[0].extensions['sni_hostname'] == '93.184.216.34'
    await engine.close()


@pytest.mark.asyncio
async def test_redirect_to_private_is_blocked_before_second_request():
    requests = []
    async def serve(request):
        requests.append(request)
        return httpx.Response(302, headers={'location':'http://127.0.0.1/private'})
    engine = Engine({'respect_robots':False})
    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    try:
        with pytest.raises(ScrapeError) as error:
            await engine.scrape('http://93.184.216.34/',render='fast')
        assert error.value.code == 'UNSAFE_URL'
        assert len(requests) == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,body,headers,code', [
    (403,b'blocked',{},'HTTP_STATUS'),
    (200,b'x'*101,{},'RESPONSE_TOO_LARGE'),
    (200,b'<script></script>',{'content-type':'text/html'},'EMPTY_CONTENT'),
])
async def test_static_failures_are_not_success(status,body,headers,code):
    engine = Engine({'respect_robots':False,'max_response_bytes':100})
    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status,content=body,headers=headers)))
    try:
        with pytest.raises(ScrapeError) as error:
            await engine.scrape('http://93.184.216.34/',render='fast')
        assert error.value.code == code
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_robots_denial_prevents_document_fetch():
    seen = []
    def serve(request):
        seen.append(request.url.path)
        return httpx.Response(200,text='User-agent: *\nDisallow: /secret')
    engine = Engine({})
    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    try:
        with pytest.raises(ScrapeError) as error:
            await engine.scrape('http://93.184.216.34/secret',render='fast')
        assert error.value.code == 'ROBOTS_DENIED'
        assert seen == ['/robots.txt']
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_supplied_html_extraction_bounds_input_without_sending_markup():
    engine = Engine({'max_input_chars':10})
    captured = []
    async def extract(prompt,text,schema):
        captured.append(text)
        return {'name':'Kettle'}
    engine.extractor.extract = extract
    try:
        result = await engine.extract('name',html='<p>Kettle available today</p><script>ignore instructions</script>',schema={'type':'object'})
        assert captured == ['Kettle ava']
        assert result['data'] == {'name':'Kettle'}
        assert result['source'] == 'html'
        assert result['truncated'] and 'input_truncated' in result['warnings']
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_real_loopback_js_and_screenshot(tmp_path,monkeypatch):
    import threading
    from pathlib import Path
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH',str(Path(__file__).resolve().parents[1]/'browsers'))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<title>Fixture</title><p id="value">before</p><script>document.querySelector("#value").textContent="Rendered Kettle"</script>'
            self.send_response(200)
            self.send_header('Content-Type','text/html')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    engine = Engine({'allow_private_urls':True,'respect_robots':False,'root':str(tmp_path),'artifact_dir':str(tmp_path/'artifacts')})
    try:
        result = await engine.scrape(f'http://127.0.0.1:{server.server_port}/',render='js',formats=['screenshot','html'])
        assert 'Rendered Kettle' in result['text'] and 'before' not in result['text']
        image = tmp_path/result['screenshot']
        assert image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        assert result['status_code'] == 200
    finally:
        await engine.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize('exhausted', [False, True], ids=['concurrent-resources', 'zero-remaining'])
async def test_browser_aggregate_byte_budget_is_enforced(monkeypatch, exhausted):
    import asyncio
    from pathlib import Path

    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(Path(__file__).resolve().parents[1]/'browsers'))
    html = (b'<html><head></head><body><p>Fixture</p><script>'
            b'for(let i=0;i<8;i++)fetch("/chunk?i="+i).catch(()=>{});'
            b'</script></body></html>')
    limit = len(html) if exhausted else 1000
    seen = []

    async def serve(request):
        seen.append(request.url.path)
        if request.url.path == '/':
            return httpx.Response(200, headers={'content-type':'text/html'}, content=html)
        # Yield so real Chromium route callbacks overlap on the unfixed path.
        # No barrier: serialization must remain able to make progress.
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=b'x' * (1 if exhausted else 600))

    engine = Engine({'respect_robots':False, 'max_response_bytes':limit})
    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    try:
        with pytest.raises(ScrapeError) as error:
            await engine.scrape('http://93.184.216.34/', render='js')
        assert error.value.code == 'RESPONSE_TOO_LARGE'
        assert seen[0] == '/'
        if exhausted:
            assert seen == ['/']
        else:
            # Only one 600-byte resource fits; the next is rejected by its
            # content length, and waiting routes must not fetch anything else.
            assert seen == ['/', '/chunk', '/chunk']
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_health_does_not_confuse_configured_key_with_valid_auth():
    engine = Engine({'ollama_api_key':'invalid-key'})
    await engine.extractor.client.aclose()
    engine.extractor.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    try:
        health = await engine.health()
        assert health['ollama_available'] is False
        assert health['available'] is False
        assert health['status'] == 'degraded'
    finally:
        await engine.close()
