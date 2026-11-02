"""The CREST (DHET) and SANLiC lists as local, searchable data.

Shared by the desktop app (journal search and matching, from files cached in
the data folder) and the phone-list upload in desktop/reflists.py.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from openpyxl import load_workbook

from .fetch import Fetcher
from .sources import sanlic
from .util import clean_text, normalise_issn, normalise_title

CREST_HOME = "https://db.crest.sun.ac.za/zapublications/"
MAX_AGE = 7 * 86400


# -------------------------------------------------------------------- CREST
def crest_file_url(html: str) -> str | None:
    m = re.search(r'href="([^"]*downloadfile/[^"]+\.xlsx)"', html)
    return urljoin(CREST_HOME, m.group(1)) if m else None


_CREST_COLS = {
    "title": ("journal title", "journal", "title", "standard title"),
    "publisher": ("publisher",),
    "issn": ("issn", "print issn", "p-issn"),
    "eissn": ("eissn", "e-issn", "online issn"),
    "key": ("journal id", "journal_id", "id"),
    "status": ("status", "journal status"),
    "reason": ("reason", "status reason", "comment", "comments", "notes"),
    "variants": ("title variants", "variants", "alternative titles"),
}


def _crest_header(row: tuple) -> dict[str, int] | None:
    cells = [clean_text(str(c)).lower() if c is not None else "" for c in row]
    if not any("issn" in c for c in cells) or not any(c in _CREST_COLS["title"] for c in cells):
        return None
    out: dict[str, int] = {}
    for key, names in _CREST_COLS.items():
        for i, c in enumerate(cells):
            if c in names and i not in out.values():
                out.setdefault(key, i)
    return out


def parse_crest_xlsx(path: Path) -> list[dict]:
    wb = load_workbook(path, read_only=True, data_only=True)
    rows: list[dict] = []
    seen: set[str] = set()
    for ws in wb.worksheets:
        header = None
        for row in ws.iter_rows(values_only=True):
            if header is None:
                header = _crest_header(row)
                continue

            def cell(k):
                i = header.get(k)
                v = row[i] if i is not None and i < len(row) else None
                return clean_text(str(v)) if v is not None else ""

            title = cell("title")
            if not title:
                continue
            issn, eissn = normalise_issn(cell("issn")), normalise_issn(cell("eissn"))
            key = cell("key") or hashlib.sha1(f"{title}|{issn}|{eissn}".encode()).hexdigest()[:16]
            if key in seen:
                continue
            seen.add(key)
            rows.append({"key": key, "title": title, "title_norm": normalise_title(title),
                         "variants": cell("variants") or None, "publisher": cell("publisher") or None,
                         "issn": issn, "eissn": eissn, "status": cell("status") or None,
                         "reason": cell("reason") or None})
    wb.close()
    if not rows:
        raise ValueError("No journals found in the CREST list (unexpected layout)")
    return rows


# ------------------------------------------------------------------- SANLiC
def sanlic_rows(path: Path) -> list[dict]:
    out, seen = [], set()
    for r in sanlic.load_records(path):
        key = hashlib.sha1(f"{r['title']}|{','.join(r['issns'])}|{r['publisher']}".encode()).hexdigest()[:16]
        if key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "title": r["title"], "title_norm": normalise_title(r["title"]),
                    "issns": r["issns"], "oa_type": r["oa_type"], "included": r["included"],
                    "explanation": r["explanation"], "subject": r["subject"], "publisher": r["publisher"]})
    return out



# ------------------------------------------------------------- local lists
def _words(q: str) -> list[str]:
    return [w for w in normalise_title(q).split() if len(w) > 1]


def crest_eligible(status: str | None) -> bool:
    return bool(re.search(r"eligible for subsidy|^active$", status or "", re.I))


class LocalLists:
    """Downloads the two lists (re-used for a week) and answers searches/matches."""

    def __init__(self, fetcher: Fetcher, folder: Path):
        self.fetcher = fetcher
        self.folder = Path(folder)
        self.crest: list[dict] | None = None
        self.sanlic: list[dict] | None = None
        self.loaded_at = 0.0
        self.errors: dict[str, str] = {}

    async def ensure(self) -> None:
        if self.crest is not None and self.sanlic is not None and time.time() - self.loaded_at < MAX_AGE:
            return
        self.errors = {}
        try:
            html = await self.fetcher.get_text(CREST_HOME)
            url = crest_file_url(html)
            if not url:
                raise ValueError("download link not found on the CREST page")
            self.crest = parse_crest_xlsx(await self.fetcher.get_file(url, MAX_AGE))
        except Exception as exc:
            self.crest = self.crest or []
            self.errors["crest"] = str(exc)
        try:
            url = None
            try:
                url = sanlic.parse_page_links(await self.fetcher.get_text(sanlic.PAGE))[0]
            except Exception:
                pass
            self.sanlic = sanlic_rows(await self.fetcher.get_file(url or sanlic.FALLBACK_LIST, MAX_AGE))
        except Exception as exc:
            self.sanlic = self.sanlic or []
            self.errors["sanlic"] = str(exc)
        self.loaded_at = time.time()

    def search_crest(self, q: str, limit: int = 50) -> list[dict]:
        words = _words(q)
        if not words:
            return []
        def hit(r):
            tokens = r["title_norm"].split()
            return all(any(t.startswith(w) for t in tokens) for w in words)
        hits = [r for r in self.crest or [] if hit(r)]
        hits.sort(key=lambda r: (not crest_eligible(r["status"]), len(r["title"])))
        return hits[:limit]

    def match(self, issns: list[str], titles: list[str]) -> dict:
        want, names = set(issns), {normalise_title(t) for t in titles if t}
        crest = [r for r in self.crest or [] if want & {r["issn"], r["eissn"]} or r["title_norm"] in names]
        sanl = [r for r in self.sanlic or [] if want & set(r["issns"]) or r["title_norm"] in names]
        return {"crest": crest, "sanlic": sanl}
