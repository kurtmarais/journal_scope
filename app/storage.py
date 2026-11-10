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

from .util import journal_key, looks_like_issn, normalise_title, outdated, report_title

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
create table if not exists saved (
    uid        text primary key,
    title      text not null default '',
    issns      text not null default '[]',
    report     text not null default '{}',
    deleted    integer not null default 0,
    dirty      integer not null default 1,
    created_at real not null,
    updated_at real not null
);
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
    def add_report(self, report: dict, uid: str | None = None, keep_created: bool = False) -> str:
        """Store a report; with `uid` it replaces that report (keeping its place in history if `keep_created`)."""
        uid = uid or uuid.uuid4().hex
        now = time.time()
        ident = report.get("identity") or {}
        report = {k: v for k, v in report.items() if k not in ("uid", "created_at", "saved")}
        with self._conn() as c:
            created = now
            if keep_created:
                cur = c.execute("select created_at from reports where uid = ?", (uid,)).fetchone()
                created = cur["created_at"] if cur else now
            c.execute(
                "insert or replace into reports (uid, query, title, issns, report, deleted, dirty, created_at, updated_at)"
                " values (?, ?, ?, ?, ?, 0, 1, ?, ?)",
                (uid, report.get("query", ""), report_title(report),
                 json.dumps(ident.get("issns") or []), json.dumps(report), created, now),
            )
        self.refresh_saved(report)   # a starred journal keeps its newest full report
        return uid

    def list_reports(self, limit: int = 200) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "select uid, query, title, issns, created_at, report from reports where deleted = 0"
                " order by created_at desc limit ?", (limit,)).fetchall()
        out = []
        for r in rows:
            rep = json.loads(r["report"])
            title = r["title"] if r["title"] and not looks_like_issn(r["title"]) else report_title(rep)
            out.append({"uid": r["uid"], "query": r["query"], "title": title,
                        "issns": json.loads(r["issns"]), "created_at": r["created_at"],
                        "glance": rep.get("glance", []), "outdated": outdated(rep)})
        return out

    def exists(self, uid: str) -> bool:
        with self._conn() as c:
            return c.execute("select 1 from reports where uid = ? and deleted = 0", (uid,)).fetchone() is not None

    def outdated_uids(self) -> list[str]:
        """Reports made before the newest sections were added, newest first."""
        with self._conn() as c:
            rows = c.execute("select uid, report from reports where deleted = 0 order by created_at desc").fetchall()
        return [r["uid"] for r in rows if outdated(json.loads(r["report"]))]

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

    # ------------------------------------------------------- saved journals
    def find_saved(self, issns: list[str], title: str | None = None) -> str | None:
        want = set(issns or [])
        tnorm = normalise_title(title)
        with self._conn() as c:
            rows = c.execute("select uid, title, issns from saved where deleted = 0").fetchall()
        for r in rows:
            if want & set(json.loads(r["issns"])) or (not want and tnorm and normalise_title(r["title"]) == tnorm):
                return r["uid"]
        return None

    def save_journal(self, report: dict) -> str:
        """Star a journal (or refresh its saved report). Returns its key."""
        ident = report.get("identity") or {}
        uid = self.find_saved(ident.get("issns") or [], ident.get("title")) or journal_key(report)
        now = time.time()
        rep = {k: v for k, v in report.items() if k not in ("uid",)}
        with self._conn() as c:
            cur = c.execute("select created_at from saved where uid = ?", (uid,)).fetchone()
            created = cur["created_at"] if cur else now
            c.execute(
                "insert or replace into saved (uid, title, issns, report, deleted, dirty, created_at, updated_at)"
                " values (?, ?, ?, ?, 0, 1, ?, ?)",
                (uid, report_title(report), json.dumps(ident.get("issns") or []),
                 json.dumps(rep), created, now))
        return uid

    def refresh_saved(self, report: dict) -> str | None:
        """If this journal is saved, keep its saved report up to date (newer full reports replace it)."""
        ident = report.get("identity") or {}
        uid = self.find_saved(ident.get("issns") or [], ident.get("title"))
        if uid:
            self.save_journal(report)
        return uid

    def unsave(self, uid: str) -> None:
        with self._conn() as c:
            c.execute("update saved set deleted = 1, dirty = 1, report = '{}', updated_at = ? where uid = ?", (time.time(), uid))

    def list_saved(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("select * from saved where deleted = 0 order by created_at desc").fetchall()
        out = []
        for r in rows:
            rep = json.loads(r["report"])
            title = r["title"] if r["title"] and not looks_like_issn(r["title"]) else report_title(rep) or r["title"]
            out.append({"uid": r["uid"], "title": title, "issns": json.loads(r["issns"]), "report": rep,
                        "created_at": r["created_at"], "updated_at": r["updated_at"]})
        return out

    def get_saved(self, uid: str) -> dict | None:
        return next((r for r in self.list_saved() if r["uid"] == uid), None)

    def dirty_saved(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("select * from saved where dirty = 1").fetchall()
        return [{"uid": r["uid"], "title": r["title"], "issns": json.loads(r["issns"]), "report": json.loads(r["report"]),
                 "deleted": bool(r["deleted"]), "created_at": r["created_at"], "updated_at": r["updated_at"]} for r in rows]

    def mark_clean_saved(self, uids_with_time: list[tuple[str, float]]) -> None:
        with self._conn() as c:
            c.executemany("update saved set dirty = 0 where uid = ? and updated_at = ?", uids_with_time)

    def merge_remote_saved(self, row: dict) -> bool:
        with self._conn() as c:
            cur = c.execute("select updated_at from saved where uid = ?", (row["uid"],)).fetchone()
            if cur and cur["updated_at"] >= row["updated_at"]:
                return False
            c.execute(
                "insert or replace into saved (uid, title, issns, report, deleted, dirty, created_at, updated_at)"
                " values (?, ?, ?, ?, ?, 0, ?, ?)",
                (row["uid"], row.get("title", ""), json.dumps(row.get("issns") or []), json.dumps(row.get("report") or {}),
                 1 if row.get("deleted") else 0, row.get("created_at") or row["updated_at"], row["updated_at"]))
        return True

    def get_meta(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            r = c.execute("select value from meta where key = ?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute("insert or replace into meta (key, value) values (?, ?)", (key, value))
