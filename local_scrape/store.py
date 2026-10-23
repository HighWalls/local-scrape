"""Short-lived SQLite connections allow API and CLI concurrency."""
import json
from contextlib import contextmanager
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path, history_limit=10000, event_limit=1000):
        self.path = str(path)
        self.history_limit = history_limit
        self.event_limit = event_limit
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, created REAL NOT NULL,
              operation TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS monitors(id INTEGER PRIMARY KEY, url TEXT NOT NULL,
              interval_seconds INTEGER NOT NULL, render TEXT NOT NULL, created REAL NOT NULL,
              next_check REAL NOT NULL, last_checked REAL, last_hash TEXT, last_text TEXT, last_error TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, monitor_id INTEGER NOT NULL,
              created REAL NOT NULL, kind TEXT NOT NULL, hash TEXT, diff TEXT, error TEXT,
              FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def record_history(self, operation, request, response):
        # Do not persist auth, source bodies or full scrape/extract payloads in history.
        safe = {k:v for k,v in request.items() if k in {"url","render","max_pages","max_depth","query","monitor_id"}}
        summary = {"ok":response["ok"]}
        if not response["ok"]:
            summary["error"] = response["error"]
        with self.connect() as db:
            db.execute("INSERT INTO history(created,operation,request,response) VALUES(?,?,?,?)",
                (time.time(),operation,json.dumps(safe),json.dumps(summary)))
            db.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT ?)", (self.history_limit,))

    def history(self, limit=50, offset=0):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM history ORDER BY id DESC LIMIT ? OFFSET ?",(limit,offset))]
            total = db.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        for row in rows:
            row["request"] = json.loads(row["request"])
            row["response"] = json.loads(row["response"])
        return dict(items=rows,total=total,limit=limit,offset=offset)

    def create_monitor(self, url, interval_seconds, render):
        now = time.time()
        with self.connect() as db:
            cur = db.execute("INSERT INTO monitors(url,interval_seconds,render,created,next_check) VALUES(?,?,?,?,?)",(url,interval_seconds,render,now,now))
            ident = cur.lastrowid
        return self.get_monitor(ident)

    def get_monitor(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT * FROM monitors WHERE id=?",(ident,)).fetchone()
        return dict(row) if row else None

    def monitors(self, limit=100, offset=0):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM monitors ORDER BY id LIMIT ? OFFSET ?",(limit,offset))]
            total = db.execute("SELECT COUNT(*) FROM monitors").fetchone()[0]
        # Last fetched content stays in SQLite, not list responses.
        for row in rows:
            row.pop("last_text",None)
        return dict(items=rows,total=total,limit=limit,offset=offset)

    def due(self):
        with self.connect() as db:
            return [r[0] for r in db.execute("SELECT id FROM monitors WHERE next_check<=? ORDER BY next_check LIMIT 100",(time.time(),))]

    def save_check(self, ident, kind, digest, text, diff, error):
        now = time.time()
        with self.connect() as db:
            row = db.execute("SELECT interval_seconds FROM monitors WHERE id=?",(ident,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE monitors SET last_checked=?, next_check=?, last_error=? WHERE id=?",(now,now+row[0],error,ident))
            if error is None:
                db.execute("UPDATE monitors SET last_hash=?,last_text=? WHERE id=?",(digest,text,ident))
            cur = db.execute("INSERT INTO events(monitor_id,created,kind,hash,diff,error) VALUES(?,?,?,?,?,?)",(ident,now,kind,digest,diff,error))
            event = dict(db.execute("SELECT * FROM events WHERE id=?",(cur.lastrowid,)).fetchone())
            db.execute("DELETE FROM events WHERE monitor_id=? AND id NOT IN (SELECT id FROM events WHERE monitor_id=? ORDER BY id DESC LIMIT ?)",(ident,ident,self.event_limit))
        return event

    def events(self, ident, limit=50, offset=0):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM events WHERE monitor_id=? ORDER BY id DESC LIMIT ? OFFSET ?",(ident,limit,offset))]
            total = db.execute("SELECT COUNT(*) FROM events WHERE monitor_id=?",(ident,)).fetchone()[0]
        return dict(items=rows,total=total,limit=limit,offset=offset)

    def delete_monitor(self, ident):
        with self.connect() as db:
            return db.execute("DELETE FROM monitors WHERE id=?",(ident,)).rowcount == 1
