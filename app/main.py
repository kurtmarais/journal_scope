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

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .fetch import Fetcher
from .paths import default_data_dir
from .report import build_report
from .storage import Database

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).parent / "static"
WEB = ROOT / "web"   # the phone app; its report renderer is shared with this UI


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.fetcher = Fetcher()
    app.state.loop = asyncio.get_running_loop()
    if getattr(app.state, "db", None) is None:
        app.state.db = Database(default_data_dir() / "journal_scope.db")
    yield
    await app.state.fetcher.close()


app = FastAPI(title="Journal Scope", lifespan=lifespan)
app.state.db = None
app.state.status = lambda: {}          # replaced by the desktop app (sync status)
app.state.on_report = lambda uid: None  # replaced by the desktop app (trigger a sync)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/web", StaticFiles(directory=WEB), name="web")


async def run_lookup(q: str, sid: str | None = None, site: str | None = None) -> dict:
    if site and not site.startswith(("http://", "https://")):
        site = "https://" + site
    return await build_report(app.state.fetcher, q, source_id=sid, site_url=site)


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
    return rep


@app.get("/api/history")
async def history():
    return app.state.db.list_reports()


@app.get("/api/history/{uid}")
async def history_item(uid: str):
    rep = app.state.db.get_report(uid)
    if rep is None:
        raise HTTPException(404, "Report not found.")
    return rep


@app.delete("/api/history/{uid}")
async def history_delete(uid: str):
    app.state.db.delete_report(uid)
    app.state.on_report(uid)
    return {"ok": True}


@app.get("/api/status")
async def status():
    return app.state.status()
