"""Journal search ("Find journals"): pass-through calls to OpenAlex, DOAJ and Scopus.

These add API keys and return the services' JSON; merging, ranking and display
happen in web/discover.js, which the desktop window and the phone share. The
phone makes the same calls through the Supabase Edge Function "discover"
(supabase/functions/discover/index.ts), which must stay in step with this file.

  title mode : journals whose titles contain the keywords (OpenAlex, DOAJ; CREST locally)
  scope mode : journals that publish the most on the keywords (OpenAlex papers, Scopus papers),
               plus DOAJ journals whose keywords/subjects match
"""

from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urlencode

from .fetch import FetchError, Fetcher

OPENALEX = "https://api.openalex.org"
DOAJ = "https://doaj.org/api/search/journals/"
SCOPUS_SEARCH = "https://api.elsevier.com/content/search/scopus"
YEARS = 5   # scope mode looks at papers from the last five years


class DiscoverError(Exception):
    pass


def clean_query(q: str) -> str:
    """Plain words only: these services each have their own query syntax."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s\-']", " ", q or "")).strip()[:200]


async def _json(fetcher: Fetcher, url: str, headers: dict | None = None) -> dict:
    try:
        resp = await fetcher.get(url, headers=headers)
    except FetchError as exc:
        raise DiscoverError(str(exc)) from exc
    if resp.status_code >= 400:
        raise DiscoverError(f"HTTP {resp.status_code}")
    try:
        return json.loads(resp.text)
    except ValueError as exc:
        raise DiscoverError("unexpected answer") from exc


async def openalex(fetcher: Fetcher, mode: str, q: str, key: str | None = None) -> dict:
    q = clean_query(q)
    extra = {"api_key": key} if key else {}
    if mode == "title":
        url = f"{OPENALEX}/sources?" + urlencode({"search": q, "filter": "type:journal", "per-page": 50, **extra})
        return {"sources": (await _json(fetcher, url)).get("results", [])}
    since = f"{date.today().year - YEARS}-01-01"
    url = f"{OPENALEX}/works?" + urlencode({
        "search": q, "filter": f"primary_location.source.type:journal,from_publication_date:{since}",
        "group_by": "primary_location.source.id", "per-page": 50, **extra})
    groups = [g for g in (await _json(fetcher, url)).get("group_by", []) if g.get("key") and g["key"] != "unknown"][:40]
    ids = "|".join(g["key"].rsplit("/", 1)[-1] for g in groups)
    sources = []
    if ids:
        url = f"{OPENALEX}/sources?" + urlencode({"filter": f"openalex:{ids}", "per-page": 50, **extra})
        sources = (await _json(fetcher, url)).get("results", [])
    return {"groups": groups, "sources": sources, "since": since}


async def doaj(fetcher: Fetcher, mode: str, q: str) -> dict:
    words = clean_query(q).split()
    if not words:
        return {"results": []}
    terms = " AND ".join(words)
    if mode == "title":
        query = f"bibjson.title:({terms})"
    else:
        query = f"bibjson.keywords:({terms}) OR bibjson.subject.term:({terms}) OR bibjson.title:({terms})"
    from urllib.parse import quote
    url = DOAJ + quote(query, safe="") + "?" + urlencode({"pageSize": 50})
    return {"results": (await _json(fetcher, url)).get("results", [])}


async def scopus_scope(fetcher: Fetcher, q: str, key: str | None, token: str | None = None) -> dict:
    """Journals with the most Scopus papers on the keywords (title, abstract, keywords)."""
    if not key:
        raise DiscoverError("no Elsevier API key (Settings → API keys)")
    words = clean_query(q)
    query = f"TITLE-ABS-KEY({words}) AND PUBYEAR > {date.today().year - YEARS - 1} AND SRCTYPE(j)"
    url = SCOPUS_SEARCH + "?" + urlencode({"query": query, "count": 1, "facets": "exactsrctitle(count=50,sort=fd)"})
    headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
    if token:
        headers["X-ELS-Insttoken"] = token
    try:
        return await _json(fetcher, url, headers)
    except DiscoverError as exc:
        if "401" in str(exc) or "403" in str(exc):
            raise DiscoverError("Scopus search needs your institution's access (university network/VPN or an institution token)") from exc
        raise
