"""Local web server behind the desktop window.

The desktop app runs this in a background thread and shows it in its own
window. It can also be run on its own for development:

    uvicorn app.main:app      then open http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import discover
from .fetch import Fetcher
from .lists import LocalLists
from .paths import default_data_dir
from .report import build_report, partial_report
from .storage import Database

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).parent / "static"
WEB = ROOT / "web"   # the phone app; its report renderer is shared with this UI


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.fetcher = Fetcher(renderer=app.state.renderer)
    app.state.loop = asyncio.get_running_loop()
    if getattr(app.state, "db", None) is None:
        app.state.db = Database(default_data_dir() / "journal_scope.db")
    app.state.lists = LocalLists(app.state.fetcher, getattr(app.state, "lists_dir", None) or default_data_dir() / "lists")
    yield
    await app.state.fetcher.close()


app = FastAPI(title="Journal Scope", lifespan=lifespan)
app.state.db = None
app.state.renderer = None        # the desktop app's Qt WebEngine renderer
app.state.scopus_credentials = lambda: (None, None)   # (API key, institution token); desktop settings
app.state.openalex_key = lambda: None                 # optional OpenAlex API key; desktop settings
app.state.status = lambda: {}          # replaced by the desktop app (sync status)
app.state.on_report = lambda uid: None  # replaced by the desktop app (trigger a sync)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/web", StaticFiles(directory=WEB), name="web")


app.state.progress = {}


async def run_lookup(q: str, sid: str | None = None, site: str | None = None) -> dict:
    if site and not site.startswith(("http://", "https://")):
        site = "https://" + site
    key, token = app.state.scopus_credentials()
    states, results = {}, {}
    app.state.progress = {"query": q, "states": states, "results": results}   # shown live in the window

    def on_progress(k, state, result=None):
        states[k] = state
        if result is not None:
            results[k] = result

    return await build_report(app.state.fetcher, q, source_id=sid, site_url=site, scopus_key=key, scopus_token=token,
                              progress=on_progress, openalex_key=app.state.openalex_key())


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


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
    rep = await run_lookup(q, sid, site)
    if not rep.get("candidates"):
        rep["uid"] = app.state.db.add_report(rep)
        app.state.on_report(rep["uid"])
        rep["saved"] = _saved_uid(rep)
    return rep


def _saved_uid(rep: dict) -> str | None:
    ident = rep.get("identity") or {}
    return app.state.db.find_saved(ident.get("issns") or [], ident.get("title"))


@app.get("/api/history")
async def history():
    return app.state.db.list_reports()


@app.get("/api/history/{uid}")
async def history_item(uid: str):
    rep = app.state.db.get_report(uid)
    if rep is None:
        raise HTTPException(404, "Report not found.")
    rep["saved"] = _saved_uid(rep)
    return rep


# ------------------------------------------------------------ saved journals
@app.get("/api/saved")
async def saved_list():
    return app.state.db.list_saved()


@app.get("/api/saved/{uid}")
async def saved_item(uid: str):
    item = app.state.db.get_saved(uid)
    if item is None:
        raise HTTPException(404, "Not in your saved journals.")
    return item


@app.post("/api/saved")
async def saved_add(report: dict = Body(...)):
    if not (report.get("identity") or {}).get("title") and not report.get("query"):
        raise HTTPException(400, "Nothing to save.")
    uid = app.state.db.save_journal(report)
    app.state.on_report(uid)
    return {"uid": uid}


@app.delete("/api/saved/{uid}")
async def saved_remove(uid: str):
    app.state.db.unsave(uid)
    app.state.on_report(uid)
    return {"ok": True}


@app.delete("/api/history/{uid}")
async def history_delete(uid: str):
    app.state.db.delete_report(uid)
    app.state.on_report(uid)
    return {"ok": True}


@app.get("/api/progress")
async def progress():
    p = app.state.progress
    if not p:
        return {"states": {}, "report": None}
    report = partial_report(p["query"], dict(p["results"])) if p["results"] else None
    return {"states": dict(p["states"]), "report": report}


# ------------------------------------------------------------ journal search
@app.get("/api/discover/{source}")
async def discover_source(source: str, q: str = Query(..., min_length=2), mode: str = Query("title")):
    if mode not in ("title", "scope", "issn"):
        raise HTTPException(400, "mode must be title, scope or issn")
    try:
        if source == "openalex":
            return await discover.openalex(app.state.fetcher, mode, q, app.state.openalex_key())
        if source == "doaj":
            return await discover.doaj(app.state.fetcher, mode, q)
        if source == "scopus":
            key, token = app.state.scopus_credentials()
            return await discover.scopus_scope(app.state.fetcher, q, key, token)
        if source == "crest":
            await app.state.lists.ensure()
            if not app.state.lists.crest and "crest" in app.state.lists.errors:
                raise discover.DiscoverError(app.state.lists.errors["crest"])
            return {"results": app.state.lists.search_crest(q)}
    except discover.DiscoverError as exc:
        raise HTTPException(502, str(exc))
    raise HTTPException(404, "unknown source")


@app.post("/api/refmatch")
async def refmatch(body: dict = Body(...)):
    """DHET (CREST) and SANLiC entries for the given ISSNs/titles, from the local lists."""
    await app.state.lists.ensure()
    issns = [str(i) for i in body.get("issns", [])][:500]
    titles = [str(t) for t in body.get("titles", [])][:200]
    return {**app.state.lists.match(issns, titles), "errors": app.state.lists.errors}


@app.get("/api/status")
async def status():
    return app.state.status()
