"""SANLiC read-and-publish (R&P) agreements (sanlic.ac.za).

Used for: whether the journal is covered by a SANLiC agreement, i.e. whether
authors at participating SA institutions can publish open access without an
APC (or at a discount), and the period of that publisher's agreement.

Data comes from SANLiC's "Journals covered by R&P agreements" spreadsheet.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from openpyxl import load_workbook

from ..fetch import FetchError, Fetcher
from ..util import clean_text, find_issns, normalise_title
from . import ERROR, FOUND, NOT_FOUND, source_result

NAME = "SANLiC (R&P agreements)"
PAGE = "https://sanlic.ac.za/read-and-publish-agreements/"
FALLBACK_LIST = "https://sanlic.ac.za/wp-content/uploads/2026/03/Alphabetical-RP-Journal-Title-List_2026.xlsx"

# Spreadsheet publisher code -> fragment of the SANLiC agreement page URL.
AGREEMENT_PAGES = {
    "ELSEVIER": "sciencedirect-elsevier",
    "TAYLOR & FRANCIS": "taylor-francis",
    "SPRINGER": "springer",
    "WILEY": "wiley",
    "SAGE": "sage-publishing",
    "OUP": "oxford-university-press",
    "CUP": "cambridge-university-press",
    "EMERALD": "emerald",
    "BENTHAM": "bentham-science",
    "ACS": "american-chemical-society",
    "ACM": "association-for-computing-machinery",
    "IOP": "iopscience",
    "RSC": "royal-society-of-chemistry",
    "AIP": "american-institute-of-physics",
    "ROYAL SOCIETY": "royal-society/",
    "THE COMPANY OF BIOLOGIST": "the-company-of-biologists",
    "THE COMPANY OF BIOLOGISTS": "the-company-of-biologists",
}

_COLS = {
    "title": ("journal title", "title"),
    "issn": ("eissn", "issn"),
    "oa_type": ("hybrid_gold_oa", "oa type", "hybrid/gold"),
    "included": ("included in r&p agreement", "included"),
    "explanation": ("explanation if not included", "explanation", "notes"),
    "subject": ("subject",),
    "publisher": ("publisher",),
}


def parse_page_links(html: str) -> tuple[str | None, list[str]]:
    """Return (title-list xlsx URL, agreement page URLs) from the R&P page."""
    soup = BeautifulSoup(html, "html.parser")
    xlsx = None
    pages = []
    for a in soup.find_all("a", href=True):
        href = urljoin(PAGE, a["href"])
        if re.search(r"Alphabetical.*\.xlsx?$", href, re.I) and xlsx is None:
            xlsx = href
        elif href.startswith("https://sanlic.ac.za/") and href.rstrip("/") != PAGE.rstrip("/"):
            pages.append(href)
    return xlsx, pages


def _header_map(row: tuple) -> dict[str, int] | None:
    cells = [clean_text(str(c)).lower() if c is not None else "" for c in row]
    if not any(c in ("journal title", "title") for c in cells):
        return None
    mapping = {}
    for key, names in _COLS.items():
        for idx, cell in enumerate(cells):
            if cell in names or any(cell.startswith(n) for n in names):
                mapping.setdefault(key, idx)
    return mapping


def load_records(path: Path) -> list[dict]:
    wb = load_workbook(path, read_only=True, data_only=True)
    records = []
    for ws in wb.worksheets:
        header = None
        for row in ws.iter_rows(values_only=True):
            if header is None:
                header = _header_map(row)
                continue
            def cell(key):
                i = header.get(key)
                v = row[i] if i is not None and i < len(row) else None
                return clean_text(str(v)) if v is not None else ""
            title = cell("title")
            if not title:
                continue
            records.append(
                {
                    "title": title,
                    "issns": find_issns(" ".join(str(v) for v in row if v is not None)),
                    "oa_type": cell("oa_type") or None,
                    "included": cell("included").upper().startswith("Y"),
                    "included_raw": cell("included") or None,
                    "explanation": cell("explanation") or None,
                    "subject": cell("subject") or None,
                    "publisher": cell("publisher") or None,
                    "sheet": ws.title,
                }
            )
    wb.close()
    return records


def match(records: list[dict], issns: list[str], title: str | None) -> dict | None:
    wanted = set(issns)
    for r in records:
        if wanted & set(r["issns"]):
            return r
    target = normalise_title(title)
    if target:
        for r in records:
            if normalise_title(r["title"]) == target:
                return r
    return None


def agreement_page_for(publisher: str | None, pages: list[str]) -> str | None:
    if not publisher:
        return None
    frag = AGREEMENT_PAGES.get(publisher.strip().upper())
    if not frag:
        return None
    for p in pages:
        if frag in p:
            return p
    return f"https://sanlic.ac.za/{frag.strip('/')}/"


def parse_agreement_period(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(class_=re.compile("entry-content|et_pb_post_content")) or soup.body or soup
    text = clean_text(root.get_text(" "))
    m = re.search(r"\b(20\d{2})\s*[-–]\s*(20\d{2})\b", text)
    return f"{m.group(1)}-{m.group(2)}" if m else None


_records_cache: dict[str, list[dict]] = {}


async def lookup(fetcher: Fetcher, title: str | None, issns: list[str]) -> dict:
    try:
        page_html = await fetcher.get_text(PAGE)
        xlsx_url, pages = parse_page_links(page_html)
    except FetchError:
        xlsx_url, pages = None, []
    xlsx_url = xlsx_url or FALLBACK_LIST
    try:
        if xlsx_url not in _records_cache:
            path = await fetcher.get_file(xlsx_url)
            _records_cache[xlsx_url] = load_records(path)
        records = _records_cache[xlsx_url]
    except FetchError as exc:
        return source_result(NAME, ERROR, PAGE, error=str(exc))
    except Exception as exc:  # malformed spreadsheet
        return source_result(NAME, ERROR, PAGE, error=f"Could not read the SANLiC title list: {exc}")

    hit = match(records, issns, title)
    if hit is None:
        return source_result(
            NAME, NOT_FOUND, PAGE,
            data={"title_list": xlsx_url, "included": False},
            note="Not on SANLiC's list of journals covered by R&P agreements; the APC is not covered by SANLiC.",
        )
    data = dict(hit)
    data["title_list"] = xlsx_url
    data["agreement_page"] = agreement_page_for(hit["publisher"], pages)
    if data["agreement_page"]:
        try:
            data["agreement_period"] = parse_agreement_period(await fetcher.get_text(data["agreement_page"]))
        except FetchError:
            data["agreement_period"] = None
    return source_result(NAME, FOUND, PAGE, data)
