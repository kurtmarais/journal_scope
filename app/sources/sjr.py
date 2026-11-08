"""SCImago Journal Rank (SJR) quartiles, from SCImago's yearly journal list.

Used for: the journal's SJR, its SJR quartile in each subject category and the
best of those (the Q1-Q4 that SCImago, and tools built on it, show), H-index.

SCImago publishes the whole ranking as one download (journalrank.php?out=xls,
a semicolon-separated CSV despite the name). The desktop app keeps a copy in
its data folder and looks journals up by ISSN, then by exact title. The copy
is renewed every 30 days; SCImago updates the ranking once a year.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import time
from pathlib import Path

from ..fetch import FetchError, looks_blocked
from ..util import clean_text, find_issns, normalise_title
from . import ERROR, FOUND, NOT_FOUND, SKIPPED, source_result

log = logging.getLogger(__name__)

NAME = "SCImago (SJR)"
DOWNLOAD = "https://www.scimagojr.com/journalrank.php?out=xls"
HOME = "https://www.scimagojr.com/journalrank.php"
MAX_AGE = 30 * 86400
QUARTILES = ("Q1", "Q2", "Q3", "Q4")

_COLS = {
    "sourceid": ("sourceid",),
    "title": ("title",),
    "type": ("type",),
    "issn": ("issn",),
    "sjr": ("sjr",),
    "quartile": ("sjr best quartile", "sjr quartile"),
    "h_index": ("h index", "h-index"),
    "country": ("country",),
    "publisher": ("publisher",),
    "coverage": ("coverage",),
    "categories": ("categories",),
    "areas": ("areas",),
}


def journal_url(sourceid: str) -> str:
    return f"https://www.scimagojr.com/journalsearch.php?q={sourceid}&tip=sid"


def _number(value: str) -> float | None:
    v = (value or "").strip().replace(" ", "")
    if not v or v == "-":
        return None
    if "," in v and "." not in v:   # SCImago writes decimals with a comma: "1,234"
        v = v.replace(",", ".")
    else:
        v = v.replace(",", "")
    try:
        return float(v)
    except ValueError:
        return None


def parse_categories(text: str) -> list[dict]:
    """'Health Informatics (Q1); Computer Science Applications (Q2)' -> [{name, quartile}]."""
    out = []
    for part in (text or "").split(";"):
        part = clean_text(part)
        if not part:
            continue
        m = re.match(r"(.*?)\s*\((Q[1-4])\)\s*$", part)
        out.append({"name": m.group(1), "quartile": m.group(2)} if m else {"name": part, "quartile": None})
    return out


def parse_csv(data: bytes | str) -> tuple[str | None, list[dict]]:
    """Return (ranking year, rows) from SCImago's download."""
    text = data.decode("utf-8-sig", "replace") if isinstance(data, bytes) else data.lstrip("﻿")
    if looks_blocked(200, text[:20000]) or "<html" in text[:2000].lower():
        raise ValueError("SCImago returned a web page instead of the journal list")
    reader = csv.reader(io.StringIO(text), delimiter=";", quotechar='"')
    header = next(reader, [])
    names = [clean_text(h).lower() for h in header]
    idx: dict[str, int] = {}
    for key, options in _COLS.items():
        for i, n in enumerate(names):
            if n in options:
                idx.setdefault(key, i)
                break
    if "title" not in idx or "issn" not in idx or "sjr" not in idx:
        raise ValueError("The SCImago list has an unexpected layout (no Title, Issn or SJR column)")
    year = next((m.group(1) for h in header if (m := re.search(r"\((\d{4})\)", h))), None)
    rows, seen = [], set()
    for rec in reader:
        def cell(k):
            i = idx.get(k)
            return clean_text(rec[i]) if i is not None and i < len(rec) else ""
        title = cell("title")
        if not title:
            continue
        key = cell("sourceid") or normalise_title(title)
        if key in seen:
            continue
        seen.add(key)
        q = cell("quartile").upper()
        h_index = _number(cell("h_index"))
        rows.append({
            "key": key, "title": title, "title_norm": normalise_title(title),
            "issns": find_issns(cell("issn")),
            "sjr": _number(cell("sjr")),
            "quartile": q if q in QUARTILES else None,
            "categories": parse_categories(cell("categories")),
            "h_index": int(h_index) if h_index is not None else None,
            "type": cell("type") or None, "publisher": cell("publisher") or None,
            "country": cell("country") or None, "coverage": cell("coverage") or None,
            "areas": cell("areas") or None, "year": year,
        })
    if not rows:
        raise ValueError("No journals found in the SCImago list")
    return year, rows


class SjrList:
    """SCImago's ranking, kept in the data folder and indexed by ISSN and title."""

    def __init__(self, fetcher, folder: Path, download=None):
        # download(url, dest) -> concurrent.futures.Future: the desktop app's embedded browser, which
        # shows SCImago's "are you a person" check for you to complete when SCImago asks for one.
        self.fetcher, self.folder, self.download = fetcher, Path(folder), download
        self.path = self.folder / "scimagojr.csv"
        self.rows: list[dict] | None = None
        self.year: str | None = None
        self.loaded_at = 0.0
        self.error: str | None = None
        self._by_issn: dict[str, dict] = {}
        self._by_title: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()

    def _index(self, year, rows) -> None:
        self.year, self.rows = year, rows
        self._by_issn, self._by_title = {}, {}
        for r in rows:
            for i in r["issns"]:
                self._by_issn.setdefault(i, r)
            self._by_title.setdefault(r["title_norm"], []).append(r)
        self.loaded_at = time.time()

    async def _fetch(self) -> bytes:
        try:
            resp = await self.fetcher.get(DOWNLOAD)
            if resp.status_code < 400 and not looks_blocked(resp.status_code, resp.text[:20000]):
                return resp.content
            reason = f"HTTP {resp.status_code}"
        except FetchError as exc:
            reason = str(exc)
        if self.download is None:
            raise FetchError(f"SCImago refused the download ({reason})")
        tmp = self.folder / "scimagojr.download.csv"
        tmp.unlink(missing_ok=True)
        await asyncio.wrap_future(self.download(DOWNLOAD, tmp))
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        return data

    async def ensure(self) -> None:
        async with self._lock:
            fresh = self.path.exists() and time.time() - self.path.stat().st_mtime < MAX_AGE
            if self.rows is not None and fresh:
                return
            if fresh:
                try:
                    self._index(*parse_csv(self.path.read_bytes()))
                    return
                except ValueError:
                    pass
            try:
                data = await self._fetch()
                year, rows = parse_csv(data)
                self.folder.mkdir(parents=True, exist_ok=True)
                self.path.write_bytes(data)
                self._index(year, rows)
                self.error = None
            except Exception as exc:
                self.error = str(exc)
                if self.rows is None and self.path.exists():   # an older copy beats none
                    try:
                        self._index(*parse_csv(self.path.read_bytes()))
                    except ValueError:
                        pass
                if self.rows is None:
                    raise FetchError(f"Could not load SCImago's journal list: {exc}") from exc

    def find(self, issns: list[str], title: str | None = None) -> dict | None:
        for i in issns or []:
            if i in self._by_issn:
                return self._by_issn[i]
        hits = self._by_title.get(normalise_title(title)) if title else None
        return hits[0] if hits and len(hits) == 1 else None

    def version(self) -> str:
        return f"SJR {self.year}" if self.year else "SJR"


def source_from_row(row: dict | None, year: str | None = None) -> dict:
    if row is None:
        return source_result(NAME, NOT_FOUND, HOME, {"year": year},
                             note="Not in SCImago's ranking (it covers journals indexed in Scopus).")
    data = {k: row.get(k) for k in ("title", "issns", "sjr", "quartile", "h_index", "type", "publisher",
                                    "country", "coverage", "areas")}
    data.update(sourceid=row["key"], year=row.get("year") or year, categories=row.get("categories") or [])
    if isinstance(data["categories"], str):          # stored as JSON text
        data["categories"] = json.loads(data["categories"])
    return source_result(NAME, FOUND, journal_url(row["key"]), data)


async def lookup(sjr_list: SjrList | None, issns: list[str], title: str | None) -> dict:
    if sjr_list is None:
        return source_result(NAME, SKIPPED, HOME, note="The SCImago list is not available here.")
    if not issns and not title:
        return source_result(NAME, SKIPPED, HOME, note="No ISSN or title to look up.")
    try:
        await sjr_list.ensure()
    except FetchError as exc:
        return source_result(NAME, ERROR, HOME, error=str(exc))
    res = source_from_row(sjr_list.find(issns, title), sjr_list.year)
    if sjr_list.error:
        res["note"] = f"Using the copy of SCImago's list from {time.strftime('%d %b %Y', time.localtime(sjr_list.path.stat().st_mtime))}; renewing it failed: {sjr_list.error}"
    return res
