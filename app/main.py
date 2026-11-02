"""Journal Scope web app.

Run:  uvicorn app.main:app   then open http://127.0.0.1:8000

Set JOURNAL_SCOPE_KEY to require an access key when the app is reachable
from other devices: open it once as  https://<host>/?key=<your key>  and the
browser remembers it.
"""

from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .fetch import Fetcher
from .report import build_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"
ACCESS_KEY = os.environ.get("JOURNAL_SCOPE_KEY") or None
COOKIE = "journal_scope_key"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.fetcher = Fetcher()
    yield
    await app.state.fetcher.close()


app = FastAPI(title="Journal Scope", lifespan=lifespan)


@app.middleware("http")
async def require_key(request: Request, call_next):
    if ACCESS_KEY is None or request.url.path.startswith("/static/") or request.url.path in ("/manifest.webmanifest", "/sw.js"):
        return await call_next(request)
    given = request.query_params.get("key")
    if given is not None and hmac.compare_digest(given, ACCESS_KEY):
        params = [(k, v) for k, v in request.query_params.multi_items() if k != "key"]
        target = request.url.path + ("?" + urlencode(params) if params else "")
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie(COOKIE, ACCESS_KEY, max_age=365 * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
        return resp
    if hmac.compare_digest(request.cookies.get(COOKIE, ""), ACCESS_KEY):
        return await call_next(request)
    return PlainTextResponse("Access key required: open this address with ?key=<your key> once.", status_code=401)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Service-Worker-Allowed": "/"})


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC / "icon-192.png", media_type="image/png")


@app.get("/api/report")
async def report(
    q: str = Query("", description="Journal title or ISSN"),
    sid: str | None = Query(None, description="SCImago source id, when picking from several matches"),
    site: str | None = Query(None, description="Journal website URL, to override the one found"),
):
    if not q.strip() and not sid:
        raise HTTPException(400, "Enter a journal title or ISSN.")
    if site and not site.startswith(("http://", "https://")):
        site = "https://" + site
    return await build_report(app.state.fetcher, q, source_id=sid, site_url=site)
