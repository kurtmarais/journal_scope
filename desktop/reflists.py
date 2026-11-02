"""Reference lists the phone searches directly (no PC needed at lookup time).

About weekly, the desktop app downloads three complete lists and loads them
into the user's Supabase project:

  SCImago  journal-rank export (CSV)      -> ref_scimago
  CREST    DHET-approved master list      -> ref_crest
  SANLiC   R&P agreement title list       -> ref_sanlic

Rows are written with a new batch id, then rows from older batches are
deleted, so the phone never sees an empty list mid-refresh.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from openpyxl import load_workbook

from app.sources import sanlic
from app.util import clean_text, find_issns, normalise_issn, normalise_title, to_float

SCIMAGO_EXPORT = "https://www.scimagojr.com/journalrank.php?out=xls"
CREST_HOME = "https://db.crest.sun.ac.za/zapublications/"
REFRESH_EVERY = 7 * 86400
RETRY_AFTER = 86400


# ------------------------------------------------------------------ SCImago
def _num(value: str | None) -> float | None:
    if value is None:
        return None
    v = value.strip().replace(" ", "")
    if not v:
        return None
    if "," in v and "." not in v:
        v = v.replace(",", ".")          # SCImago uses decimal commas
    return to_float(v)


def parse_scimago_csv(data: bytes) -> tuple[list[dict], int | None]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = next(reader, [])
    cols = {h.strip().lower(): i for i, h in enumerate(header)}

    def find(*names: str) -> int | None:
        for n in names:
            for h, i in cols.items():
                if h == n or h.startswith(n):
                    return i
        return None

    year = None
    for h in header:
        m = re.search(r"total docs\.?\s*\((\d{4})\)", h, re.I)
        if m:
            year = int(m.group(1))
    idx = {
        "key": find("sourceid"), "title": find("title"), "type": find("type"), "issn": find("issn"),
        "sjr": find("sjr"), "q": find("sjr best quartile"), "h": find("h index"),
        "cites": find("cites / doc. (2years)", "citations / doc. (2years)", "cites/doc. (2years)"),
        "country": find("country"), "publisher": find("publisher"), "coverage": find("coverage"),
        "categories": find("categories"), "areas": find("areas"),
    }
    if idx["key"] is None or idx["title"] is None:
        raise ValueError("Not a SCImago journal-rank export (no Sourceid/Title columns)")

    def cell(row, k):
        i = idx[k]
        return row[i].strip() if i is not None and i < len(row) else ""

    rows = []
    for row in reader:
        key = cell(row, "key")
        if not key:
            continue
        h = cell(row, "h")
        q = cell(row, "q")
        rows.append({
            "key": key,
            "title": cell(row, "title"),
            "title_norm": normalise_title(cell(row, "title")),
            "type": cell(row, "type") or None,
            "issns": find_issns(cell(row, "issn")),
            "sjr": _num(cell(row, "sjr")),
            "sjr_quartile": q if q in ("Q1", "Q2", "Q3", "Q4") else None,
            "sjr_year": year,
            "h_index": int(h) if h.isdigit() else None,
            "cites_doc_2y": _num(cell(row, "cites")),
            "country": cell(row, "country") or None,
            "publisher": cell(row, "publisher") or None,
            "coverage": cell(row, "coverage") or None,
            "categories": cell(row, "categories") or None,
            "areas": cell(row, "areas") or None,
        })
    return rows, year


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


# ------------------------------------------------------------------ refresh
def due(settings: dict, now: float | None = None) -> bool:
    now = now or time.time()
    last_ok = settings.get("ref_refreshed_at", 0)
    last_try = settings.get("ref_tried_at", 0)
    return now - last_ok > REFRESH_EVERY and now - last_try > RETRY_AFTER


def refresh(client, token: Callable[[], str], get_bytes: Callable[[str], bytes],
            get_scimago: Callable[[], bytes], tmp_dir: Path) -> dict[str, str]:
    """Download and upload all three lists. Returns {list: 'ok N rows' | 'failed: why'}."""
    batch = hashlib.sha1(str(time.time()).encode()).hexdigest()[:10]
    results: dict[str, str] = {}
    tmp_dir.mkdir(parents=True, exist_ok=True)

    def load(name: str, table: str, rows: list[dict], version: str, url: str) -> None:
        for r in rows:
            r["batch"] = batch
        client.upsert(table, rows, token(), on_conflict="user_id,key", chunk=1000)
        client.delete(table, {"batch": f"neq.{batch}"}, token())
        client.upsert("ref_lists", [{"key": name, "version": version, "source_url": url, "rows": len(rows),
                                     "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}],
                      token(), on_conflict="user_id,key")
        results[name] = f"ok, {len(rows)} journals"

    steps = []

    def do_sanlic():
        page = get_bytes(sanlic.PAGE).decode("utf-8", "replace")
        url = sanlic.parse_page_links(page)[0] or sanlic.FALLBACK_LIST
        path = tmp_dir / "sanlic.xlsx"
        path.write_bytes(get_bytes(url))
        load("sanlic", "ref_sanlic", sanlic_rows(path), url.rsplit("/", 1)[-1], url)

    def do_crest():
        url = crest_file_url(get_bytes(CREST_HOME).decode("utf-8", "replace"))
        if not url:
            raise ValueError("download link not found on the CREST page")
        path = tmp_dir / "crest.xlsx"
        path.write_bytes(get_bytes(url))
        load("crest", "ref_crest", parse_crest_xlsx(path), url.rsplit("/", 1)[-1], url)

    def do_scimago():
        rows, year = parse_scimago_csv(get_scimago())
        load("scimago", "ref_scimago", rows, f"SJR {year}" if year else "latest", SCIMAGO_EXPORT)

    steps = [("sanlic", do_sanlic), ("crest", do_crest), ("scimago", do_scimago)]
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:   # one list failing must not stop the others
            results[name] = f"failed: {exc}"
    return results
