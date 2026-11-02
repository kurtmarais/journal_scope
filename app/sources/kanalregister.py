"""Norwegian Register for Scientific Journals (kanalregister.hkdir.no).

Used for: credibility -- the register's level (2 = leading, 1 = approved
scientific channel, 0 = not approved, X = under discussion), its level
history, DOAJ indexing and any ceased-publication flag.

The site renders in the browser, so pages are loaded with headless Chromium.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote_plus, urlparse

from bs4 import BeautifulSoup

import json

from ..fetch import CACHE_DIR, FetchError, Fetcher
from ..util import clean_text, find_issns, normalise_title
from . import ERROR, FOUND, NOT_FOUND, source_result

NAME = "Kanalregister (Norwegian Register)"
BASE = "https://kanalregister.hkdir.no"

LEVEL_MEANING = {
    "2": "Level 2 - leading scientific channel",
    "1": "Level 1 - approved scientific channel",
    "0": "Level 0 - not approved as a scientific channel",
    "X": "Level X - under discussion",
}


def search_url(term: str) -> str:
    return f"{BASE}/en/sok?option=journals&input={quote_plus(term)}"


def journal_url(jid: str) -> str:
    return f"{BASE}/en/tidsskrift?id={jid}"


def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select('a[href*="tidsskrift?id="]'):
        jid = parse_qs(urlparse(a["href"]).query).get("id", [None])[0]
        if not jid or jid in seen:
            continue
        seen.add(jid)
        h3 = a.find(["h3", "h2"])
        out.append({"id": jid, "title": clean_text(h3.get_text()) if h3 else clean_text(a.get_text())[:200]})
    return out


def _level_code(text: str) -> str | None:
    m = re.search(r"\b(?:Level|Nivå)\s*([012X])\b", text)
    if m:
        return m.group(1)
    if re.search(r"Not approved|Ikke godkjent", text):
        return "0"
    if re.search(r"Under discussion|Under diskusjon", text):
        return "X"
    return None


def parse_journal(html: str, url: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    root = soup.find("main") or soup.body or soup
    lines = [clean_text(s) for s in root.get_text("\n").split("\n")]
    lines = [s for s in lines if s]

    data: dict = {"url": url}

    # Basic information table (label: value), with the real href for URL.
    info: dict[str, str] = {}
    for tr in root.select("table tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2:
            label = clean_text(cells[0].get_text()).rstrip(":")
            info[label] = clean_text(cells[1].get_text())
            link = cells[1].find("a", href=True)
            if label.lower() == "url" and link:
                info[label] = link["href"]
    data["title"] = info.get("Title") or info.get("Tittel")
    data["publisher"] = info.get("Publisher") or info.get("Utgiver")
    data["country"] = info.get("Country of publication") or info.get("Utgiverland")
    data["homepage"] = info.get("URL")
    issn_text = " ".join(v for k, v in info.items() if "ISSN" in k)
    data["issns"] = find_issns(issn_text)

    # Status badges shown under the title.
    status_lines: list[str] = []
    if "Status:" in lines:
        i = lines.index("Status:") + 1
        while i < len(lines) and lines[i] not in ("Basic information", "Grunnleggende informasjon"):
            status_lines.append(lines[i])
            i += 1
        if data["title"] is None and lines.index("Status:") > 0:
            data["title"] = lines[lines.index("Status:") - 1]
    status_text = " ".join(status_lines)
    data["level"] = _level_code(status_text)
    data["level_meaning"] = LEVEL_MEANING.get(data["level"] or "")
    ceased = re.search(r"(?:Nedlagt i|Discontinued in|Ceased in)\s*(\d{4})", status_text)
    data["ceased"] = int(ceased.group(1)) if ceased else None

    # Indexing.
    doaj = next((s for s in lines if "Directory of Open Access Journals" in s), None)
    data["doaj"] = None if doaj is None else not re.match(r"(Not|Ikke)\b", doaj)

    # Decision note, rendered as "dd.mm.yyyy: text".
    note = next((s for s in lines if re.match(r"\d{2}\.\d{2}\.\d{4}:\s", s)), None)
    data["decision_note"] = note

    # Level history: a year line followed by "Scientific Level:" and the value.
    history = []
    for i, s in enumerate(lines[:-2]):
        if re.fullmatch(r"(19|20)\d{2}", s) and lines[i + 1] in ("Scientific Level:", "Nivå:"):
            history.append({"year": int(s), "level": lines[i + 2]})
    data["level_history"] = history
    if data["level"] is None and history:
        data["level"] = history[0]["level"]
        data["level_meaning"] = LEVEL_MEANING.get(data["level"])
    return data


_ID_CACHE = CACHE_DIR / "kanalregister_ids.json"


def _cached_id(issns: list[str]) -> str | None:
    try:
        known = json.loads(_ID_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return next((known[i] for i in issns if i in known), None)


def _remember(issns: list[str], jid: str) -> None:
    try:
        known = json.loads(_ID_CACHE.read_text(encoding="utf-8")) if _ID_CACHE.exists() else {}
        known.update({i: jid for i in issns})
        _ID_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _ID_CACHE.write_text(json.dumps(known), encoding="utf-8")
    except (OSError, ValueError):
        pass


async def lookup(fetcher: Fetcher, title: str | None, issns: list[str]) -> dict:
    terms = list(issns) or ([title] if title else [])
    if not terms:
        return source_result(NAME, NOT_FOUND, BASE, note="No ISSN or title to search with.")
    try:
        jid = _cached_id(issns)   # known from an earlier lookup: skip the search page
        for term in ([] if jid else terms):
            url = search_url(term)
            html = await fetcher.render(url, wait_for='a[href*="tidsskrift?id="]', wait_ms=8000)
            hits = parse_search(html)
            if hits and term in issns:
                jid = hits[0]["id"]
                break
            target = normalise_title(title)
            exact = [h for h in hits if normalise_title(h["title"]) == target]
            if exact:
                jid = exact[0]["id"]
                break
        if jid is None and title and issns and not _cached_id(issns):
            html = await fetcher.render(search_url(title), wait_for='a[href*="tidsskrift?id="]', wait_ms=8000)
            exact = [h for h in parse_search(html) if normalise_title(h["title"]) == normalise_title(title)]
            jid = exact[0]["id"] if exact else None
        if jid is None:
            return source_result(
                NAME, NOT_FOUND, search_url(terms[0]),
                note="Not in the Norwegian Register, so it has not been assessed as a scientific channel.",
            )
        url = journal_url(jid)
        html = await fetcher.render(url, wait_for="table", wait_ms=15000)
        data = parse_journal(html, url)
        data["id"] = jid
        if not data.get("title"):
            return source_result(NAME, ERROR, url, error="Could not read the Kanalregister journal page.")
        _remember(sorted(set(issns) | set(data.get("issns") or [])), jid)
        return source_result(NAME, FOUND, url, data)
    except FetchError as exc:
        return source_result(NAME, ERROR, search_url(terms[0]), error=str(exc))

