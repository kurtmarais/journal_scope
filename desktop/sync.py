"""Phone sync through Supabase.

The desktop keeps its report history in local SQLite and exchanges it with
the user's own Supabase project over the REST API (no extra packages).

One sync cycle (runs on a worker thread):
  1. upload reports created or deleted on the PC
  2. download reports changed elsewhere (e.g. deleted on the phone)
  3. run lookups the phone has asked for, upload the reports, mark them done

The PC does the lookups because they need a browser and Python; the phone
only reads reports and queues requests.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from pathlib import Path

from app.storage import Database

from . import winplatform

PAGE = 1000
TIMEOUT = 30
REPORT_FIELDS = ("uid", "query", "title", "issns", "report", "deleted", "created_at", "updated_at")


class SyncError(Exception):
    pass


class AuthError(SyncError):
    """Sign-in rejected or session expired: the user must sign in again."""


Opener = Callable[..., Any]


class SupabaseClient:
    def __init__(self, url: str, key: str, opener: Opener | None = None):
        self.url = url.rstrip("/")
        self.key = key.strip()
        self._open = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, body: Any = None, token: str | None = None,
                 params: dict | None = None, headers: dict | None = None) -> Any:
        url = f"{self.url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe=",.()*:")
        # The publishable key goes in "apikey"; only a signed-in user's token goes in Authorization.
        h = {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self._open(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (400, 401, 403) and ("/auth/" in path or "JWT" in detail or exc.code == 401):
                raise AuthError(_message(detail) or f"Sign-in failed ({exc.code})") from exc
            raise SyncError(f"{exc.code}: {_message(detail) or detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SyncError(f"Cannot reach Supabase ({getattr(exc, 'reason', exc)})") from exc
        return json.loads(raw) if raw else None

    # ------------------------------------------------------------------ auth
    def _session(self, data: dict) -> dict:
        return {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": time.time() + int(data.get("expires_in", 3600)),
            "email": (data.get("user") or {}).get("email", ""),
        }

    def sign_in(self, email: str, password: str) -> dict:
        return self._session(self._request("POST", "/auth/v1/token", {"email": email, "password": password},
                                           params={"grant_type": "password"}))

    def refresh(self, refresh_token: str) -> dict:
        return self._session(self._request("POST", "/auth/v1/token", {"refresh_token": refresh_token},
                                           params={"grant_type": "refresh_token"}))

    # ------------------------------------------------------------------ data
    def changes_since(self, table: str, cursor: str, token: str) -> list[dict]:
        rows: list[dict] = []
        while True:
            params = {"select": "*", "order": "server_updated_at.asc", "limit": str(PAGE)}
            if cursor:
                params["server_updated_at"] = f"gt.{cursor}"
            page = self._request("GET", f"/rest/v1/{table}", token=token, params=params) or []
            rows.extend(page)
            if len(page) < PAGE:
                return rows
            cursor = page[-1]["server_updated_at"]

    def upsert(self, table: str, rows: list[dict], token: str, on_conflict: str = "uid", chunk: int = 25) -> None:
        # Reports are a few tens of KB each (chunks of 25); reference-list rows are small.
        for i in range(0, len(rows), chunk):
            self._request("POST", f"/rest/v1/{table}", rows[i:i + chunk], token=token,
                          params={"on_conflict": on_conflict},
                          headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    def delete(self, table: str, filters: dict, token: str) -> None:
        self._request("DELETE", f"/rest/v1/{table}", token=token, params=filters,
                      headers={"Prefer": "return=minimal"})

    def pending_requests(self, token: str) -> list[dict]:
        return self._request("GET", "/rest/v1/journal_requests", token=token, params={
            "select": "*", "status": "eq.pending", "deleted": "eq.false", "order": "created_at.asc"}) or []

    def update_request(self, uid: str, fields: dict, token: str) -> None:
        self._request("PATCH", "/rest/v1/journal_requests", dict(fields, updated_at=time.time()), token=token,
                      params={"uid": f"eq.{uid}"}, headers={"Prefer": "return=minimal"})


def _message(detail: str) -> str:
    try:
        d = json.loads(detail)
    except ValueError:
        return ""
    return d.get("error_description") or d.get("msg") or d.get("message") or d.get("error") or ""


# --------------------------------------------------------- token storage
def protect_token(token: str) -> str:
    """Refresh tokens are stored encrypted for this Windows account."""
    if winplatform.dpapi_available():
        return "dpapi:" + base64.b64encode(winplatform.dpapi_protect(token.encode(), b"journal-scope-sync")).decode()
    return "plain:" + token


def unprotect_token(stored: str) -> str:
    if not stored:
        return ""
    kind, _, value = stored.partition(":")
    if kind == "dpapi":
        try:
            return winplatform.dpapi_unprotect(base64.b64decode(value), b"journal-scope-sync").decode()
        except Exception:
            return ""
    return value if kind == "plain" else ""


# ------------------------------------------------------------------ engine
Lookup = Callable[[str, "str | None", "str | None"], dict]


class SyncEngine:
    """Holds sync settings and performs cycles. Not thread-safe: one cycle at a time."""

    def __init__(self, db: Database, settings: dict, save_settings: Callable[[dict], None],
                 lookup: Lookup, opener: Opener | None = None,
                 get_bytes: Callable[[str], bytes] | None = None,
                 tmp_dir: Path | None = None):
        self.db = db
        self.get_bytes = get_bytes
        self.tmp_dir = tmp_dir
        self.force_lists = False
        self.settings = settings
        self._save = save_settings
        self.lookup = lookup
        self.opener = opener
        self._session: dict | None = None

    # -------------------------------------------------------------- config
    @property
    def configured(self) -> bool:
        s = self.settings
        return bool(s.get("sync_url") and s.get("sync_key") and s.get("sync_token"))

    def client(self) -> SupabaseClient:
        return SupabaseClient(self.settings.get("sync_url", ""), self.settings.get("sync_key", ""), self.opener)

    def sign_in(self, url: str, key: str, email: str, password: str) -> str:
        """Sign in and store the refresh token (encrypted). The password is not stored."""
        client = SupabaseClient(url, key, self.opener)
        session = client.sign_in(email, password)
        self.settings.update(sync_url=url.rstrip("/"), sync_key=key.strip(), sync_email=session["email"] or email,
                             sync_token=protect_token(session["refresh_token"]), sync_cursor="")
        self._save(self.settings)
        self._session = session
        return session["email"] or email

    def sign_out(self) -> None:
        for k in ("sync_token", "sync_cursor"):
            self.settings.pop(k, None)
        self._save(self.settings)
        self._session = None

    def _token(self) -> str:
        if self._session and self._session["expires_at"] - time.time() > 120:
            return self._session["access_token"]
        refresh = unprotect_token(self.settings.get("sync_token", ""))
        if not refresh:
            raise AuthError("Not signed in")
        self._session = self.client().refresh(refresh)
        self.settings["sync_token"] = protect_token(self._session["refresh_token"])
        self._save(self.settings)
        return self._session["access_token"]

    # --------------------------------------------------------------- cycle
    def _upload_dirty(self, client: SupabaseClient, token: str) -> int:
        rows = self.db.dirty_rows()
        if rows:
            client.upsert("journal_reports", [{k: r[k] for k in REPORT_FIELDS} for r in rows], token)
            self.db.mark_clean([(r["uid"], r["updated_at"]) for r in rows])
        return len(rows)

    def cycle(self) -> dict:
        """One full sync. Returns counts; raises SyncError/AuthError."""
        client = self.client()
        token = self._token()
        uploaded = self._upload_dirty(client, token)

        cursor = self.settings.get("sync_cursor", "")
        downloaded = 0
        for row in client.changes_since("journal_reports", cursor, token):
            if self.db.merge_remote(row):
                downloaded += 1
            cursor = row.get("server_updated_at") or cursor
        if cursor != self.settings.get("sync_cursor", ""):
            self.settings["sync_cursor"] = cursor
            self._save(self.settings)

        looked_up = 0
        for req in client.pending_requests(token):
            client.update_request(req["uid"], {"status": "running"}, token)
            try:
                rep = self.lookup(req.get("query") or "", req.get("sid"), req.get("site"))
            except Exception as exc:
                client.update_request(req["uid"], {"status": "error", "error": str(exc)[:500]}, self._token())
                continue
            token = self._token()   # a lookup can take a minute; keep the session fresh
            if rep.get("candidates"):
                client.update_request(req["uid"], {"status": "done", "candidates": rep["candidates"]}, token)
            else:
                uid = self.db.add_report(rep)
                self._upload_dirty(client, token)
                client.update_request(req["uid"], {"status": "done", "report_uid": uid}, token)
            looked_up += 1

        saved_error = None
        try:
            saved = self.db.dirty_saved()
            if saved:
                client.upsert("journal_saved", [{k: r[k] for k in REPORT_FIELDS if k in r} for r in saved], token)
                self.db.mark_clean_saved([(r["uid"], r["updated_at"]) for r in saved])
            cur = self.settings.get("sync_cursor_saved", "")
            for row in client.changes_since("journal_saved", cur, token):
                if self.db.merge_remote_saved(row):
                    downloaded += 1
                cur = row.get("server_updated_at") or cur
            if cur != self.settings.get("sync_cursor_saved", ""):
                self.settings["sync_cursor_saved"] = cur
                self._save(self.settings)
        except AuthError:
            raise
        except SyncError as exc:
            saved_error = ("run supabase/migrations/20261105090000_saved_journals.sql in Supabase"
                           if "journal_saved" in str(exc) or "404" in str(exc) else str(exc))

        lists = None
        from . import reflists   # imported on first use: keeps start-up fast
        if self.get_bytes and (self.force_lists or reflists.due(self.settings)):
            lists = self.refresh_lists(client)
        return {"uploaded": uploaded, "downloaded": downloaded, "looked_up": looked_up, "lists": lists, "saved_error": saved_error}

    def refresh_lists(self, client: SupabaseClient | None = None) -> dict[str, str]:
        """Reload the reference lists the phone searches (about weekly)."""
        from . import reflists
        self.force_lists = False
        self.settings["ref_tried_at"] = time.time()
        results = reflists.refresh(client or self.client(), self._token, self.get_bytes, self.tmp_dir or Path("."))
        if all(v.startswith("ok") for v in results.values()):
            self.settings["ref_refreshed_at"] = time.time()
        self.settings["ref_results"] = results
        self._save(self.settings)
        return results
