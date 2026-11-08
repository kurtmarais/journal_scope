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
from .report import _glance, build_report, partial_report
from .sources import sjr
from .storage import Database
from .util import REPORT_SECTIONS, normalise_title, refresh_query

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
    lists_dir = getattr(app.state, "lists_dir", None) or default_data_dir() / "lists"
    app.state.lists = LocalLists(app.state.fetcher, lists_dir)
    app.state.sjr = sjr.SjrList(app.state.fetcher, lists_dir, download=app.state.download)
    yield
    await app.state.fetcher.close()


app = FastAPI(title="Journal Scope", lifespan=lifespan)
app.state.db = None
app.state.renderer = None        # the desktop app's Qt WebEngine renderer
app.state.download = None        # its file download (SCImago's list, when SCImago asks for a human check)
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
                              progress=on_progress, openalex_key=app.state.openalex_key(), sjr_list=app.state.sjr)


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


# ------------------------------------------------------------ updating older reports
app.state.bulk = {"running": False, "done": 0, "total": 0, "failed": [], "current": None}


async def redo_report(uid: str, live: bool = False) -> dict:
    """Run a stored report again with every current source and replace it in place."""
    old = app.state.db.get_report(uid)
    if old is None:
        raise HTTPException(404, "Report not found.")
    missing = [k for k in REPORT_SECTIONS if k not in (old.get("sources") or {})]
    if missing == ["sjr"]:   # only the SJR list is new: add it without running every source again
        rep = await add_sjr(old)
        app.state.db.add_report(rep, uid=uid, keep_created=True)
        app.state.on_report(uid)
        rep = app.state.db.get_report(uid)
        rep["saved"] = _saved_uid(rep)
        return rep
    q = refresh_query(old)
    if not q:
        raise HTTPException(400, "This report has no title or ISSN to search for.")
    if live:
        rep = await run_lookup(q)
    else:
        key, token = app.state.scopus_credentials()
        rep = await build_report(app.state.fetcher, q, scopus_key=key, scopus_token=token,
                                 openalex_key=app.state.openalex_key(), sjr_list=app.state.sjr)
    if rep.get("candidates"):
        raise HTTPException(409, f"“{q}” now matches several journals; search for it by ISSN instead.")
    app.state.db.add_report(rep, uid=uid, keep_created=True)
    app.state.on_report(uid)
    rep = app.state.db.get_report(uid)
    rep["saved"] = _saved_uid(rep)
    return rep


async def add_sjr(report: dict) -> dict:
    """An existing report with SCImago's SJR section added (from the downloaded list; no other source re-run)."""
    ident = report.get("identity") or {}
    rep = {k: v for k, v in report.items() if k not in ("uid", "created_at", "saved")}
    rep["sources"] = dict(rep.get("sources") or {})
    res = await sjr.lookup(app.state.sjr, ident.get("issns") or [], ident.get("title"))
    if res["status"] == "error":
        raise HTTPException(502, res["error"])
    rep["sources"]["sjr"] = res
    rep["glance"] = _glance(rep)
    return rep


@app.post("/api/history/{uid}/refresh")
async def history_refresh(uid: str):
    return await redo_report(uid, live=True)


@app.get("/api/refresh")
async def refresh_status():
    b = app.state.bulk
    return dict(b, failed=list(b["failed"]), outdated=len(app.state.db.outdated_uids()))


@app.post("/api/refresh")
async def refresh_start(body: dict = Body(default={})):
    """Update older reports one after another in the background (all reports with {"all": true})."""
    b = app.state.bulk
    if b["running"]:
        return await refresh_status()
    uids = [r["uid"] for r in app.state.db.list_reports(limit=1000)] if body.get("all") else app.state.db.outdated_uids()
    b.update(running=bool(uids), done=0, total=len(uids), failed=[], current=None)

    async def worker():
        try:
            for uid in uids:
                rep = app.state.db.get_report(uid)
                if rep is None:
                    b["done"] += 1
                    continue
                b["current"] = (rep.get("identity") or {}).get("title") or rep.get("query")
                try:
                    await redo_report(uid)
                except Exception as exc:   # keep going; the old report stays as it was
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    b["failed"].append({"uid": uid, "title": b["current"], "error": str(detail)[:200]})
                b["done"] += 1
        finally:
            b.update(running=False, current=None)

    app.state.bulk_task = asyncio.create_task(worker())
    return await refresh_status()


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
    errors = dict(app.state.lists.errors)
    rows: list[dict] = []
    try:
        # A first download can wait for SCImago's human check; don't hold the search results for it.
        await asyncio.wait_for(asyncio.shield(app.state.sjr.ensure()), 20)
        want, names = set(issns), {normalise_title(t) for t in titles if t}
        rows = [r for r in app.state.sjr.rows or [] if want & set(r["issns"]) or r["title_norm"] in names]
    except asyncio.TimeoutError:
        errors["sjr"] = "SCImago's list is still downloading; search again in a minute"
    except Exception as exc:
        errors["sjr"] = str(exc)
    return {**app.state.lists.match(issns, titles), "sjr": rows, "errors": errors}


async def sjr_rows() -> tuple[str, str, list[dict]]:
    """(version, source URL, rows) of SCImago's list, for the phone's copy in Supabase."""
    await app.state.sjr.ensure()
    return app.state.sjr.version(), sjr.DOWNLOAD, app.state.sjr.rows or []


@app.get("/api/status")
async def status():
    return app.state.status()
