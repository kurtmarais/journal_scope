"""In-memory stand-in for the parts of Supabase the apps use.

Mimics password/refresh-token auth, PostgREST select (eq./gt. filters, order,
limit), insert, upsert (merge-duplicates on uid), PATCH by uid, and the
newest-edit-wins trigger. Usable as a urllib opener (desktop tests) or as a
real HTTP server with CORS (phone tests).
"""

from __future__ import annotations

import io
import itertools
import json
import threading
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EMAIL, PASSWORD = "me@example.org", "correct horse"
TABLES = ("journal_reports", "journal_requests")
DEFAULTS = {
    "journal_reports": {"query": "", "title": "", "issns": [], "report": {}, "deleted": False},
    "journal_requests": {"query": "", "sid": None, "site": None, "status": "pending", "report_uid": None,
                         "candidates": None, "error": None, "deleted": False},
}


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, dict[str, dict]] = {t: {} for t in TABLES}
        self.lock = threading.Lock()
        self._tick = itertools.count(1)
        self._tokens = itertools.count(1)
        self.access: set[str] = set()
        self.refresh: set[str] = set()
        self.base = datetime(2026, 11, 3, tzinfo=timezone.utc)

    # ------------------------------------------------------------- core
    def _stamp(self) -> str:
        return (self.base + timedelta(milliseconds=next(self._tick))).isoformat()

    def _session(self) -> dict:
        n = next(self._tokens)
        a, r = f"access-{n}", f"refresh-{n}"
        self.access.add(a)
        self.refresh.add(r)
        return {"access_token": a, "refresh_token": r, "expires_in": 3600, "user": {"id": "u1", "email": EMAIL}}

    def _write(self, table: str, row: dict, merge: bool) -> None:
        cur = self.tables[table].get(row["uid"])
        if cur is not None:
            if not merge:
                raise ValueError("duplicate key")
            if row.get("updated_at", 0) < cur.get("updated_at", 0):
                return   # newest edit wins
            cur.update(row)
            cur["server_updated_at"] = self._stamp()
        else:
            new = {**DEFAULTS[table], "created_at": 0, "updated_at": 0, **row, "server_updated_at": self._stamp()}
            self.tables[table][row["uid"]] = new

    def handle(self, method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, object]:
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        data = json.loads(body) if body else None
        h = {k.lower(): v for k, v in headers.items()}
        with self.lock:
            if parts.path == "/auth/v1/token":
                if q.get("grant_type") == "password":
                    if data.get("email") == EMAIL and data.get("password") == PASSWORD:
                        return 200, self._session()
                    return 400, {"error_description": "Invalid login credentials"}
                if data.get("refresh_token") in self.refresh:
                    self.refresh.discard(data["refresh_token"])
                    return 200, self._session()
                return 400, {"error_description": "Invalid Refresh Token"}
            if not parts.path.startswith("/rest/v1/"):
                return 404, {"message": "not found"}
            table = parts.path.rsplit("/", 1)[-1]
            if table not in self.tables:
                return 404, {"message": f"relation {table} does not exist"}
            if h.get("authorization", "").removeprefix("Bearer ") not in self.access:
                return 401, {"message": "JWT expired"}
            filters = {k: v for k, v in q.items() if k not in ("select", "order", "limit", "on_conflict")}
            if method == "GET":
                rows = [r for r in self.tables[table].values() if self._match(r, filters)]
                if "order" in q:
                    col, _, direction = q["order"].partition(".")
                    rows.sort(key=lambda r: r.get(col) or 0, reverse=direction == "desc")
                rows = rows[: int(q.get("limit", 10_000))]
                return 200, json.loads(json.dumps(rows))
            if method == "POST":
                merge = "merge-duplicates" in h.get("prefer", "")
                for row in data if isinstance(data, list) else [data]:
                    try:
                        self._write(table, row, merge)
                    except ValueError:
                        return 409, {"message": "duplicate key value violates unique constraint"}
                return 201, None
            if method == "PATCH":
                for r in [r for r in self.tables[table].values() if self._match(r, filters)]:
                    self._write(table, {**data, "uid": r["uid"]}, merge=True)
                return 204, None
        return 405, {"message": "method not allowed"}

    @staticmethod
    def _match(row: dict, filters: dict) -> bool:
        for col, cond in filters.items():
            op, _, val = cond.partition(".")
            v = row.get(col)
            if op == "eq":
                if str(v).lower() != val.lower() and not (isinstance(v, bool) and str(v).lower() == val):
                    return False
            elif op == "gt":
                if v is None or str(v) <= val:
                    return False
        return True

    # ------------------------------------------------------ urllib opener
    def opener(self, req, timeout=None):
        status, payload = self.handle(req.get_method(), req.full_url, dict(req.header_items()), req.data)
        raw = json.dumps(payload).encode() if payload is not None else b""
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "error", {}, io.BytesIO(raw))
        return _Resp(raw)

    # --------------------------------------------------------- HTTP server
    def serve(self) -> tuple[ThreadingHTTPServer, str]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def _any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else None
                status, payload = fake.handle(self.command, self.path, dict(self.headers), body)
                raw = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PATCH = _any

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class _Resp:
    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
