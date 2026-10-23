import pytest
from local_scrape.settings import load_settings, token_for
from local_scrape.store import Store


def test_settings_are_bounded_and_token_stable(tmp_path):
    s = load_settings(root=tmp_path)
    assert s['api_host'] == '127.0.0.1'
    assert s['allow_private_urls'] is False
    assert token_for(s) == token_for(s)
    assert len(token_for(s)) >= 40
    for override in ({'api_host':'0.0.0.0'}, {'max_pages':1001}, {'ollama_url':'https://evil.example'}, {'data_dir':'../escape'}, {'nonsense':1}):
        with pytest.raises(ValueError):
            load_settings(root=tmp_path, overrides=override)


def test_store_closes_connections_and_retains_only_bounded_rows(tmp_path,monkeypatch):
    import sqlite3
    import local_scrape.store as module
    original=sqlite3.connect
    connections=[]
    class Tracked(sqlite3.Connection):
        closed=False
        def close(self):
            self.closed=True
            super().close()
    def connect(*args,**kwargs):
        db=original(*args,**kwargs,factory=Tracked)
        connections.append(db)
        return db
    monkeypatch.setattr(module.sqlite3,'connect',connect)
    store=Store(tmp_path/'state.sqlite3',history_limit=2,event_limit=2)
    monitor=store.create_monitor('https://example.com',60,'fast')
    for n in range(4):
        store.record_history('scrape',{'text':'not persisted'},{'ok':True})
        store.save_check(monitor['id'],'unchanged',str(n),'text','',None)
    assert store.history()['total']==2
    assert store.events(monitor['id'])['total']==2
    assert all(db.closed for db in connections)


def test_store_persists_history_monitor_and_events(tmp_path):
    db = tmp_path / 'state.sqlite3'
    s = Store(db)
    m = s.create_monitor('https://example.com', 60, 'fast')
    s.record_history('scrape', {'url':'https://example.com'}, {'ok':True,'data':{'text':'hello'}})
    e = s.save_check(m['id'], 'first', 'abc', 'hello', '', None)
    assert e['kind'] == 'first'
    s = Store(db)
    assert s.get_monitor(m['id'])['last_hash'] == 'abc'
    assert s.history(10,0)['items'][0]['operation'] == 'scrape'
    assert len(s.events(m['id'],10,0)['items']) == 1
    assert s.delete_monitor(m['id']) is True
    assert s.get_monitor(m['id']) is None
