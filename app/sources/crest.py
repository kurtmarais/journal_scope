"""CREST ZA Publications Monitor (db.crest.sun.ac.za/zapublications).

Used for: whether articles in the journal are eligible for DHET subsidy.
A journal that is absent from this list is not DHET-approved.
"""

from __future__ import annotations

from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..fetch import FetchError, Fetcher
from ..util import clean_text, normalise_issn, normalise_title
from . import ERROR, FOUND, NOT_FOUND, SKIPPED, source_result

NAME = "CREST (DHET subsidy list)"
HOME = "https://db.crest.sun.ac.za/zapublications/"
SEARCH = "https://db.crest.sun.ac.za/zapublications/journal-search"


def search_url(term: str) -> str:
    return f"{SEARCH}?{urlencode({'search': term})}"


def parse_rows(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("#tbl_journal_search tbody tr"):
        title_cell = tr.find("th")
        cells = tr.find_all("td")
        if title_cell is None or len(cells) < 5:
            continue
        title_el = title_cell.select_one(".journal-title-text") or title_cell
        status_el = cells[4].select_one(".status-pill")
        status = clean_text(status_el.get("data-status") or status_el.get_text()) if status_el else clean_text(cells[4].get_text())
        status = status.replace("ⓘ", "").strip()
        rows.append(
            {
                "title": clean_text(title_el.get_text()),
                "title_variants": [clean_text(li.get_text()) for li in title_cell.select(".title-variants-source li")],
                "publisher": clean_text(cells[0].get_text()) or None,
                "issn": normalise_issn(cells[1].get_text()),
                "eissn": normalise_issn(cells[2].get_text()),
                "journal_id": clean_text(cells[3].get_text()) or None,
                "status": status,
                "eligible": status.lower() == "eligible for subsidy",
                "reason": (status_el.get("data-reason") if status_el else None) or None,
            }
        )
    return rows


def match(rows: list[dict], issns: list[str], title: str | None) -> dict | None:
    wanted = set(issns)
    for r in rows:
        if wanted & {r["issn"], r["eissn"]} - {None}:
            return r
    target = normalise_title(title)
    if target:
        for r in rows:
            names = [r["title"], *r["title_variants"]]
            if any(normalise_title(n) == target for n in names):
                return r
    return None


async def lookup(fetcher: Fetcher, title: str | None, issns: list[str]) -> dict:
    if not title:
        return source_result(NAME, SKIPPED, HOME, note="CREST can only be searched by journal title, and no title was found for this ISSN.")
    url = search_url(title)
    try:
        html = await fetcher.get_text(url, headers={"X-Requested-With": "XMLHttpRequest"})
        rows = parse_rows(html)
        hit = match(rows, issns, title)
        if hit is None and "&" in title:
            alt = await fetcher.get_text(search_url(title.replace("&", "and")), headers={"X-Requested-With": "XMLHttpRequest"})
            hit = match(parse_rows(alt), issns, title)
    except FetchError as exc:
        return source_result(NAME, ERROR, url, error=str(exc))
    if hit is None:
        return source_result(
            NAME,
            NOT_FOUND,
            url,
            data={"eligible": False},
            note="Not on the DHET-approved journal list, so articles are not eligible for subsidy.",
        )
    return source_result(NAME, FOUND, url, hit)
