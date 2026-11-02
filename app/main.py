"""Journal Scope web app.

Run:  uvicorn app.main:app --reload   then open http://127.0.0.1:8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .fetch import Fetcher
from .report import build_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.fetcher = Fetcher()
    yield
    await app.state.fetcher.close()


app = FastAPI(title="Journal Scope", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


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
