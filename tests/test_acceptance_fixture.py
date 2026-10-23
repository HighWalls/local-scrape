"""Lead acceptance: controlled real HTTP fixture, not simulated upstream results."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from local_scrape.core import Engine
from local_scrape.settings import load_settings
from local_scrape.store import Store
from local_scrape.workflows import Workflows


@pytest.mark.asyncio
async def test_real_http_monitor_changes_and_sqlite_reopen(tmp_path):
    state = {"price": "1.25", "requests": 0}

    class Fixture(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"] += 1
            if self.path == "/robots.txt":
                content = b"User-agent: *\nAllow: /\n"
                kind = "text/plain"
            elif self.path == "/product":
                content = ("<!doctype html><title>Controlled monitor fixture</title>"
                           "<h1>Fixture product</h1><p>Price: " + state["price"] + "</p>").encode()
                kind = "text/html"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Explicit isolated trusted test setting; production API has no request bypass.
    settings = load_settings(root=tmp_path, overrides={
        "allow_private_urls": True,
        "credential_env_file": None,
        "timeout_seconds": 10,
    })
    engine = Engine(settings)
    db = tmp_path / "data" / "fixture.sqlite3"
    store = Store(db)
    workflows = Workflows(engine, store, settings)
    try:
        monitor = await workflows.create_monitor(
            f"http://127.0.0.1:{server.server_port}/product", 60, "fast")
        first = await workflows.check_monitor(monitor["id"])
        unchanged = await workflows.check_monitor(monitor["id"])
        state["price"] = "2.50"
        changed = await workflows.check_monitor(monitor["id"])
        assert [first["kind"], unchanged["kind"], changed["kind"]] == ["first", "unchanged", "changed"]
        assert "1.25" in changed["diff"] and "2.50" in changed["diff"]
        reopened = Store(db)
        assert reopened.get_monitor(monitor["id"])["last_hash"] == changed["hash"]
        events = reopened.events(monitor["id"])
        assert events["total"] == 3
        assert events["items"][0]["kind"] == "changed"
        assert state["requests"] == 6  # A robots check and document request per check.
        assert reopened.delete_monitor(monitor["id"])
        assert reopened.get_monitor(monitor["id"]) is None
    finally:
        await engine.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
