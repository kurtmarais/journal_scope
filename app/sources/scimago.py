"""SCImago Journal & Country Rank (scimagojr.com).

Used for: existence/indexing in Scopus (coverage), SJR and quartiles, H-index,
2-year citations per document, homepage link and similar journals.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from ..fetch import FetchError, Fetcher
from ..util import clean_text, find_issns, normalise_title, to_float
from . import ERROR, FOUND, NOT_FOUND, source_result

BASE = "https://www.scimagojr.com/"
NAME = "SCImago"


def search_url(query: str) -> str:
    return f"{BASE}journalsearch.php?q={quote_plus(query)}"


def detail_url(source_id: str) -> str:
    return f"{BASE}journalsearch.php?q={source_id}&tip=sid&clean=0"


def _source_id(href: str) -> str | None:
    qs = parse_qs(urlparse(href).query)
    if qs.get("tip", [""])[0] == "sid":
        return qs.get("q", [None])[0]
    return None


def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("div.search_results a[href]"):
        sid = _source_id(a["href"])
        if not sid:
            continue
        name = a.select_one(".jrnlname")
        item = a.select_one(".sresult_item")
        title = clean_text(name.get_text()) if name else ""
        rest = []
        if item:
            for node in item.contents:
                if node is name:
                    continue
                text = clean_text(node.get_text() if hasattr(node, "get_text") else str(node))
                if text:
                    rest.append(text)
        out.append(
            {
                "source_id": sid,
                "title": title,
                "country": rest[0] if rest else None,
                "publisher": rest[1] if len(rest) > 1 else None,
                "url": detail_url(sid),
            }
        )
    return out


def _blocks(soup: BeautifulSoup) -> dict[str, object]:
    """Map each `div.cuadrado` heading to its element."""
    blocks = {}
    for box in soup.select("div.cuadrado"):
        h2 = box.find("h2")
        if h2:
            blocks[clean_text(h2.get_text())] = box
    return blocks


def _table_rows(table) -> list[list[str]]:
    return [
        [clean_text(td.get_text()) for td in tr.find_all("td")]
        for tr in table.select("tbody tr")
    ]


def _dashboard_table(soup: BeautifulSoup, title_contains: str):
    for cell in soup.select("div.dynamiccell"):
        t = cell.select_one(".celltitle")
        if t and title_contains.lower() in clean_text(t.get_text()).lower():
            return cell.find("table")
    return None


def parse_detail(html: str, url: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data: dict = {}

    h1 = soup.select_one("div.journaldescription h1") or soup.find("h1")
    data["title"] = clean_text(h1.get_text()) if h1 else None

    blocks = _blocks(soup)

    if "Country" in blocks:
        a = blocks["Country"].select_one(".cuadrado-detail a")
        data["country"] = clean_text(a.get_text()) if a else None

    if "Publisher" in blocks:
        a = blocks["Publisher"].select_one(".cuadrado-detail a")
        data["publisher"] = clean_text((a or blocks["Publisher"].select_one(".cuadrado-detail")).get_text())

    if "Subject Area and Category" in blocks:
        areas = []
        for li in blocks["Subject Area and Category"].select(".cuadrado-detail > ul > li"):
            area = li.find("a")
            cats = [clean_text(c.get_text()) for c in li.select("ul.treecategory a")]
            areas.append({"area": clean_text(area.get_text()) if area else None, "categories": cats})
        data["subject_areas"] = areas

    for heading, box in blocks.items():
        m = re.fullmatch(r"SJR (\d{4})", heading)
        if m:
            value = box.select_one(".hsjr")
            quart = box.find("span", class_=re.compile(r"^Q[1-4]$"))
            data["sjr"] = {
                "year": int(m.group(1)),
                "value": to_float(value.get_text()) if value else None,
                "best_quartile": clean_text(quart.get_text()) if quart else None,
            }

    if "H-Index" in blocks:
        n = blocks["H-Index"].select_one(".hindexnumber")
        data["h_index"] = int(clean_text(n.get_text())) if n and clean_text(n.get_text()).isdigit() else None

    if "Publication type" in blocks:
        data["publication_type"] = clean_text(blocks["Publication type"].select_one(".cuadrado-detail").get_text())

    if "ISSN" in blocks:
        data["issns"] = find_issns(blocks["ISSN"].get_text())

    if "Coverage" in blocks:
        cov = clean_text(blocks["Coverage"].select_one(".cuadrado-detail").get_text())
        data["coverage"] = cov
        years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", cov)]
        data["coverage_last_year"] = max(years) if years else None

    if "Information" in blocks:
        for a in blocks["Information"].select("a[href]"):
            label = clean_text(a.get_text()).lower()
            href = a["href"]
            if label == "home":
                data["homepage"] = href
            elif "how to publish" in label:
                data["how_to_publish"] = href

    # Quartile per category, most recent year only.
    qcell = soup.select_one("div.quartiles-graph")
    qtable = qcell.find("table") if qcell else None
    if qtable:
        latest: dict[str, tuple[int, str]] = {}
        for row in _table_rows(qtable):
            if len(row) == 3 and row[1].isdigit():
                cat, year, q = row[0], int(row[1]), row[2]
                if cat not in latest or year > latest[cat][0]:
                    latest[cat] = (year, q)
        data["category_quartiles"] = [
            {"category": c, "year": y, "quartile": q} for c, (y, q) in latest.items()
        ]

    # Citations per document (2 years) -- SCImago's closest analogue to an impact factor.
    ctable = _dashboard_table(soup, "Citations per document")
    if ctable:
        series = []
        for row in _table_rows(ctable):
            if len(row) == 3 and "2 years" in row[0] and row[1].isdigit():
                series.append({"year": int(row[1]), "value": to_float(row[2])})
        series.sort(key=lambda r: r["year"])
        data["cites_per_doc_2y"] = series[-5:]

    # Similar journals, as SCImago lists them.
    similar = []
    for a in soup.select("#related_slider a.slider_item[href]"):
        sim = a.select_one(".related_similarity")
        similar.append(
            {
                "title": a.get("title") or clean_text(a.select_one(".related_sourcetitle").get_text()),
                "url": urljoin(BASE, a["href"]),
                "similarity": clean_text(sim.get_text()) if sim else None,
                "open_access": a.select_one("img.openaccessicon") is not None,
            }
        )
    data["similar_journals"] = similar[:12]

    # Any discontinuation notice SCImago shows on the page.
    desc = soup.select_one("div.journaldescription")
    text = clean_text(desc.get_text(" ")) if desc else ""
    m = re.search(r"[^.]*discontinued[^.]*\.?", text, re.I)
    data["notice"] = clean_text(m.group(0)) if m else None

    data["url"] = url
    return data


def choose(candidates: list[dict], query: str, issn: str | None) -> dict | None:
    """Pick the single matching search result, or None if the user must choose."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if issn:  # an ISSN search with several hits: SCImago lists the exact one first
        return candidates[0]
    target = normalise_title(query)
    exact = [c for c in candidates if normalise_title(c["title"]) == target]
    return exact[0] if len(exact) == 1 else None


async def search(fetcher: Fetcher, query: str) -> list[dict]:
    html, _ = await fetcher.page_html(search_url(query), wait_for="div.search_results")
    return parse_search(html)


async def lookup(fetcher: Fetcher, query: str, issn: str | None, source_id: str | None = None) -> tuple[dict, list[dict]]:
    """Return (result, candidates). `candidates` is non-empty when the query is ambiguous."""
    try:
        candidates: list[dict] = []
        if not source_id:
            candidates = await search(fetcher, issn or query)
            if not candidates and issn and query != issn:
                candidates = await search(fetcher, query)
            picked = choose(candidates, query, issn)
            if picked is None:
                if not candidates:
                    return (
                        source_result(NAME, NOT_FOUND, search_url(issn or query),
                                      note="No match in SCImago. The journal is not (or no longer) indexed in Scopus."),
                        [],
                    )
                return source_result(NAME, NOT_FOUND, search_url(query), note="Several journals match; choose one."), candidates
            source_id = picked["source_id"]
        url = detail_url(source_id)
        html, _ = await fetcher.page_html(url, wait_for="div.journalgrid")
        data = parse_detail(html, url)
        if not data.get("title"):
            return source_result(NAME, ERROR, url, error="Could not read the SCImago journal page."), []
        data["source_id"] = source_id
        return source_result(NAME, FOUND, url, data), []
    except FetchError as exc:
        return source_result(NAME, ERROR, search_url(issn or query), error=str(exc)), []
