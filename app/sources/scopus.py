"""Scopus, via Elsevier's Serial Title API (api.elsevier.com).

Used for: existence and indexing in Scopus, CiteScore with its rank and
percentile in each Scopus subject (CiteScore quartile: Q1 = 75th percentile
and above), SJR, publisher and the journal homepage.

Needs a free Elsevier API key (dev.elsevier.com). CiteScore ranks are part
of the "CITESCORE" view, which Elsevier ties to institutional access (your
university network or an institution token). Without it the basic view is
used: CiteScore without ranks.
"""

from __future__ import annotations

import json
import os
from urllib.parse import quote, urlencode

from ..fetch import FetchError, Fetcher
from ..util import clean_text, find_issns, normalise_title, to_float
from . import ERROR, FOUND, NOT_FOUND, SKIPPED, source_result

NAME = "Scopus (CiteScore)"
API = "https://api.elsevier.com/content/serial/title"
SOURCES_PAGE = "https://www.scopus.com/sources"


def quartile(percentile: float | None) -> str | None:
    if percentile is None:
        return None
    return "Q1" if percentile >= 75 else "Q2" if percentile >= 50 else "Q3" if percentile >= 25 else "Q4"


def _list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _year_value(items) -> dict | None:
    best = None
    for it in _list(items):
        y, val = it.get("@year"), to_float(it.get("$"))
        if y and str(y).isdigit() and val is not None and (best is None or int(y) > best["year"]):
            best = {"year": int(y), "value": val}
    return best


def entries(payload: dict) -> list[dict]:
    resp = payload.get("serial-metadata-response") or {}
    out = []
    for e in _list(resp.get("entry")):
        if isinstance(e, dict) and "error" not in e and (e.get("dc:title") or e.get("source-id")):
            out.append(e)
    return out


def parse_entry(e: dict, limited: bool = False) -> dict:
    subjects = {str(s.get("@code")): clean_text(s.get("$")) for s in _list(e.get("subject-area")) if s.get("@code")}
    links = {l.get("@ref"): l.get("@href") for l in _list(e.get("link")) if isinstance(l, dict)}
    issns = find_issns(" ".join(filter(None, [e.get("prism:issn"), e.get("prism:eIssn")])))

    cs = e.get("citeScoreYearInfoList") or {}
    citescore = None
    ranks: list[dict] = []
    if cs.get("citeScoreCurrentMetric") is not None:
        citescore = {"year": int(cs["citeScoreCurrentMetricYear"]) if str(cs.get("citeScoreCurrentMetricYear", "")).isdigit() else None,
                     "value": to_float(cs.get("citeScoreCurrentMetric"))}
    tracker = None
    if cs.get("citeScoreTracker") is not None:
        tracker = {"year": int(cs["citeScoreTrackerYear"]) if str(cs.get("citeScoreTrackerYear", "")).isdigit() else None,
                   "value": to_float(cs.get("citeScoreTracker"))}

    # Ranks for the latest complete year (view=CITESCORE only).
    complete = [y for y in _list(cs.get("citeScoreYearInfo")) if (y.get("@status") or "").lower() == "complete"]
    complete.sort(key=lambda y: int(y.get("@year", 0)), reverse=True)
    if complete:
        year_info = complete[0]
        year = int(year_info.get("@year", 0)) or None
        for info_list in _list(year_info.get("citeScoreInformationList")):
            for info in _list(info_list.get("citeScoreInfo")):
                if (info.get("docType") or "all") != "all":
                    continue
                if citescore is None or citescore.get("year") == year:
                    citescore = {"year": year, "value": to_float(info.get("citeScore"))}
                for r in _list(info.get("citeScoreSubjectRank")):
                    pct = to_float(r.get("percentile"))
                    code = str(r.get("subjectCode"))
                    ranks.append({"subject": subjects.get(code) or code, "code": code, "year": year,
                                  "rank": r.get("rank"), "percentile": pct, "quartile": quartile(pct)})
    ranks.sort(key=lambda r: -(r["percentile"] or 0))

    start, end = e.get("coverageStartYear"), e.get("coverageEndYear")
    data = {
        "title": clean_text(e.get("dc:title")),
        "publisher": clean_text(e.get("dc:publisher")) or None,
        "source_id": str(e.get("source-id") or "") or None,
        "type": e.get("prism:aggregationType"),
        "issns": issns,
        "subjects": list(subjects.values()),
        "citescore": citescore,
        "citescore_tracker": tracker,
        "subject_ranks": ranks,
        "best_quartile": ranks[0]["quartile"] if ranks else None,
        "best_percentile": ranks[0]["percentile"] if ranks else None,
        "sjr": _year_value((e.get("SJRList") or {}).get("SJR")),
        "coverage": f"{start}-{end}" if start and end else None,
        "coverage_last_year": int(end) if str(end or "").isdigit() else None,
        "homepage": links.get("homepage"),
        "scopus_url": links.get("scopus-source"),
        "ranks_unavailable": not ranks and (limited or citescore is not None),
    }
    return data


def candidates_from(items: list[dict]) -> list[dict]:
    out = []
    for e in items:
        issns = find_issns(" ".join(filter(None, [e.get("prism:issn"), e.get("prism:eIssn")])))
        if not issns:
            continue
        out.append({"source_id": issns[0], "title": clean_text(e.get("dc:title")),
                    "publisher": clean_text(e.get("dc:publisher")) or None, "country": None})
    return out


def choose(cands: list[dict], query: str) -> dict | None:
    if len(cands) == 1:
        return cands[0]
    exact = [c for c in cands if normalise_title(c["title"]) == normalise_title(query)]
    return exact[0] if len(exact) == 1 else None


class ScopusError(Exception):
    pass


async def _get(fetcher: Fetcher, url: str, key: str, token: str | None) -> tuple[int, dict]:
    headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
    if token:
        headers["X-ELS-Insttoken"] = token
    resp = await fetcher.get(url, headers=headers)
    try:
        body = json.loads(resp.text) if resp.text else {}
    except ValueError:
        body = {}
    return resp.status_code, body


def _error_text(status: int, body: dict) -> str:
    err = body.get("service-error", {}).get("status", {}) if isinstance(body, dict) else {}
    detail = err.get("statusText") or err.get("statusCode") or ""
    if status == 401:
        return f"Elsevier rejected the API key ({detail or 'authentication error'}). Check it in Settings → API keys."
    if status == 403:
        return f"Elsevier refused access ({detail or 'not entitled'})."
    if status == 429:
        return "Elsevier's weekly API quota for this key is used up."
    return f"Scopus API error {status} {detail}".strip()


async def detail(fetcher: Fetcher, issn: str, key: str, token: str | None) -> tuple[dict | None, str | None]:
    """Returns (data, note). data is None when the ISSN is not in Scopus."""
    # CITESCORE has the subject ranks; keys without that entitlement get 400/403,
    # so fall back to ENHANCED, then STANDARD (CiteScore value, no ranks).
    limited = False
    for view in ("CITESCORE", "ENHANCED", "STANDARD"):
        status, body = await _get(fetcher, f"{API}/issn/{quote(issn)}?view={view}", key, token)
        if status not in (400, 403):
            break
        limited = True
    if status == 404:
        return None, None
    if status >= 400:
        raise ScopusError(_error_text(status, body))
    items = entries(body)
    if not items:
        return None, None
    data = parse_entry(items[0], limited=limited)
    note = ("CiteScore ranks and quartiles need institutional access (use the PC on your university network or VPN, "
            "or add an institution token in Settings).") if data["ranks_unavailable"] else None
    return data, note


async def lookup(fetcher: Fetcher, query: str, issn: str | None, source_id: str | None = None,
                 key: str | None = None, token: str | None = None) -> tuple[dict, list[dict]]:
    """Return (result, candidates). `source_id` here is an ISSN picked from candidates."""
    key = key or os.environ.get("ELSEVIER_API_KEY") or None
    token = token or os.environ.get("ELSEVIER_INSTTOKEN")
    if not key:
        return source_result(NAME, SKIPPED, SOURCES_PAGE,
                             note="Add your free Elsevier API key (Settings → API keys) to check Scopus and CiteScore."), []
    try:
        target = source_id or issn
        if not target:
            status, body = await _get(fetcher, f"{API}?{urlencode({'title': query, 'count': 25})}", key, token)
            if status >= 400 and status != 404:
                raise ScopusError(_error_text(status, body))
            cands = candidates_from(entries(body)) if status != 404 else []
            picked = choose(cands, query)
            if picked is None:
                if not cands:
                    return source_result(NAME, NOT_FOUND, SOURCES_PAGE,
                                         note="No journal with this title in Scopus, so it is not (or no longer) indexed there."), []
                return source_result(NAME, NOT_FOUND, SOURCES_PAGE, note="Several journals match; choose one."), cands
            target = picked["source_id"]
        data, note = await detail(fetcher, target, key, token)
        if data is None:
            return source_result(NAME, NOT_FOUND, SOURCES_PAGE,
                                 note="Not in Scopus, so the journal is not (or no longer) indexed there."), []
        return source_result(NAME, FOUND, data.get("scopus_url") or SOURCES_PAGE, data, note=note), []
    except (FetchError, ScopusError) as exc:
        return source_result(NAME, ERROR, SOURCES_PAGE, error=str(exc)), []
