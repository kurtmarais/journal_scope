"""Local report history (SQLite), shared by the web UI and phone sync.

Each call opens its own connection, so the web server thread and the sync
thread can use the same database safely.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
create table if not exists reports (
    uid        text primary key,
    query      text not null default '',
    title      text not null default '',
    issns      text not null default '[]',
    report     text not null default '{}',
    deleted    integer not null default 0,
    dirty      integer not null default 1,
    created_at real not null,
    updated_at real not null
);
create index if not exists reports_recent on reports (deleted, created_at);
create table if not exists meta (key text primary key, value text);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------ reports
    def add_report(self, report: dict, uid: str | None = None) -> str:
        uid = uid or uuid.uuid4().hex
        now = time.time()
        ident = report.get("identity") or {}
        with self._conn() as c:
            c.execute(
                "insert or replace into reports (uid, query, title, issns, report, deleted, dirty, created_at, updated_at)"
                " values (?, ?, ?, ?, ?, 0, 1, ?, ?)",
                (uid, report.get("query", ""), ident.get("title") or report.get("query", ""),
                 json.dumps(ident.get("issns") or []), json.dumps(report), now, now),
            )
        return uid

    def list_reports(self, limit: int = 200) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "select uid, query, title, issns, created_at, report from reports where deleted = 0"
                " order by created_at desc limit ?", (limit,)).fetchall()
        out = []
        for r in rows:
            rep = json.loads(r["report"])
            out.append({"uid": r["uid"], "query": r["query"], "title": r["title"],
                        "issns": json.loads(r["issns"]), "created_at": r["created_at"],
                        "glance": rep.get("glance", [])})
        return out

    def get_report(self, uid: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("select report, created_at from reports where uid = ? and deleted = 0", (uid,)).fetchone()
        if not r:
            return None
        rep = json.loads(r["report"])
        rep["uid"], rep["created_at"] = uid, r["created_at"]
        return rep

    def delete_report(self, uid: str) -> None:
        with self._conn() as c:
            c.execute("update reports set deleted = 1, dirty = 1, report = '{}', updated_at = ? where uid = ?",
                      (time.time(), uid))

    # ---------------------------------------------------------- sync helpers
    def dirty_rows(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("select * from reports where dirty = 1").fetchall()
        return [{"uid": r["uid"], "query": r["query"], "title": r["title"],
                 "issns": json.loads(r["issns"]), "report": json.loads(r["report"]),
                 "deleted": bool(r["deleted"]), "created_at": r["created_at"],
                 "updated_at": r["updated_at"]} for r in rows]

    def mark_clean(self, uids_with_time: list[tuple[str, float]]) -> None:
        """Clear the dirty flag unless the row changed again since it was read."""
        with self._conn() as c:
            c.executemany("update reports set dirty = 0 where uid = ? and updated_at = ?",
                          [(u, t) for u, t in uids_with_time])

    def merge_remote(self, row: dict) -> bool:
        """Apply a downloaded row if it is newer than the local copy."""
        with self._conn() as c:
            cur = c.execute("select updated_at from reports where uid = ?", (row["uid"],)).fetchone()
            if cur and cur["updated_at"] >= row["updated_at"]:
                return False
            c.execute(
                "insert or replace into reports (uid, query, title, issns, report, deleted, dirty, created_at, updated_at)"
                " values (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (row["uid"], row.get("query", ""), row.get("title", ""), json.dumps(row.get("issns") or []),
                 json.dumps(row.get("report") or {}), 1 if row.get("deleted") else 0,
                 row.get("created_at") or row["updated_at"], row["updated_at"]),
            )
        return True

    def get_meta(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            r = c.execute("select value from meta where key = ?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute("insert or replace into meta (key, value) values (?, ?)", (key, value))
