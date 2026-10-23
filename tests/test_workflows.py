import pytest
from local_scrape.settings import load_settings
from local_scrape.store import Store
from local_scrape.workflows import Workflows, WorkflowError


class Engine:
    def __init__(self): self.calls=[]; self.text='before'
    async def scrape(self,url,**kwargs):
        self.calls.append(url)
        if url.endswith('/bad'): raise WorkflowError('FETCH_FAILED','blocked',502)
        return dict(url=url,final_url=url,text=self.text,links=[{'url':'https://example.com/a#fragment','text':'a'},{'url':'https://other.test/','text':'other'},{'url':'https://example.com/bad','text':'bad'},{'url':'https://example.com/a','text':'duplicate'}],warnings=[],truncated=False)


def workflows(tmp_path, engine=None, search=None):
    s=load_settings(root=tmp_path)
    return Workflows(engine or Engine(),Store(tmp_path/'test.db'),s,search_provider=search)


@pytest.mark.asyncio
async def test_crawl_bounds_dedup_errors(tmp_path):
    w=workflows(tmp_path)
    r=await w.crawl('https://example.com/',max_pages=3,max_depth=1)
    assert len(r['pages']) == 2
    assert len(r['errors']) == 1
    assert len(w.engine.calls) == 3
    assert not any('other.test' in u for u in w.engine.calls)
    with pytest.raises(WorkflowError): await w.crawl('https://example.com',max_pages=101)


@pytest.mark.asyncio
@pytest.mark.parametrize('redirect_to_root', [True, False], ids=['existing-root', 'distinct-queued-final'])
@pytest.mark.parametrize('max_pages', [2, 3], ids=['attempt-limit', 'queue-drained'])
async def test_crawl_deduplicates_collected_final_urls(tmp_path, redirect_to_root, max_pages):
    root, alias, next_url = ('https://example.com/', 'https://example.com/alias',
                             'https://example.com/next')

    class RedirectEngine:
        def __init__(self):
            self.calls = []

        async def scrape(self, url, **kwargs):
            self.calls.append(url)
            final = (root if redirect_to_root else next_url) if url == alias else url
            links = [{'url':alias}, {'url':next_url}] if url == root else []
            return dict(url=url, final_url=final, text=final, links=links,
                        warnings=[], truncated=False)

    engine = RedirectEngine()
    w = workflows(tmp_path, engine)
    result = await w.crawl(root, max_pages=max_pages, max_depth=1)
    expected = [root] if redirect_to_root and max_pages == 2 else [root, next_url]
    assert [page['final_url'] for page in result['pages']] == expected
    assert engine.calls == [root, alias, next_url][:max_pages]
    assert result['attempted'] == max_pages
    assert result['errors'] == []
    assert result['warnings'] == (['page_limit_reached'] if max_pages == 2 else [])
    assert result['partial'] is (max_pages == 2)


@pytest.mark.asyncio
async def test_monitor_real_hash_diff_and_persistence(tmp_path):
    e=Engine(); w=workflows(tmp_path,e)
    m=await w.create_monitor('https://example.com',60,'fast')
    first=await w.check_monitor(m['id'])
    unchanged=await w.check_monitor(m['id'])
    e.text='after'
    changed=await w.check_monitor(m['id'])
    assert first['kind']=='first' and unchanged['kind']=='unchanged' and changed['kind']=='changed'
    assert first['hash'] != changed['hash']
    assert '-before' in changed['diff'] and '+after' in changed['diff']
    assert w.store.events(m['id'])['total']==3


@pytest.mark.asyncio
async def test_search_truthful_and_bounded(tmp_path):
    def provider(query,limit): return [{'title':'Actual','href':'https://example.com','body':'Snippet'}]
    w=workflows(tmp_path,search=provider)
    r=await w.search('query',max_results=1)
    assert r['results'][0]['url']=='https://example.com/'
    def empty(query,limit): return []
    w.search_provider=empty
    with pytest.raises(WorkflowError,match='no results'): await w.search('query')
    with pytest.raises(WorkflowError): await w.search('query',max_results=99)
