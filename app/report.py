"""Build one journal report from all sources."""

from __future__ import annotations

import asyncio
from datetime import date

import time

from .fetch import Fetcher
from .sources import ERROR, FOUND, apc, crest, guidelines, issn_portal, journal_site, kanalregister, sanlic, scopus, sjr, source_result
from .util import looks_like_issn, normalise_issn, report_title


def _glance(report: dict) -> list[dict]:
    """Headline values, each taken straight from one source."""
    working = {"status": "working", "data": {}}
    s = {k: report["sources"].get(k) or working for k in ("scopus", "crest", "kanalregister", "sanlic", "journal_site", "apc", "sjr")}
    out: list[dict] = []
    indexed = None   # "Indexed in Scopus" goes last; the two quartile tiles (SJR, CiteScore) lead

    sj = s["sjr"]
    if sj["status"] == FOUND and (sj["data"].get("quartile") or sj["data"].get("sjr") is not None):
        d = sj["data"]
        value = d.get("quartile") or "No quartile"
        if d.get("sjr") is not None:
            value += f" (SJR {d['sjr']:.3f})"
        out.append({"label": f"SJR quartile {d['year']}" if d.get("year") else "SJR quartile", "value": value,
                    "tone": {"Q1": "good", "Q2": "good", "Q3": "warn", "Q4": "bad"}.get(d.get("quartile"), "neutral"),
                    "source": "sjr"})
    elif sj["status"] == "not_found" and not report.get("candidates"):
        out.append({"label": "SJR quartile", "value": "Not ranked by SCImago", "tone": "warn", "source": "sjr"})

    sc = s["scopus"]
    if sc["status"] == FOUND:
        d = sc["data"]
        last = d.get("coverage_last_year") or (d.get("citescore") or {}).get("year")
        current = last is not None and last >= date.today().year - 2
        indexed = {
            "label": "Indexed in Scopus",
            "value": f"Coverage {d['coverage']}" if d.get("coverage") else "Listed",
            "tone": "good" if current else "warn",
            "source": "scopus",
        }
        cs = d.get("citescore")
        if cs and cs.get("value") is not None:
            q, pct = d.get("best_quartile"), d.get("best_percentile")
            extra = f" ({q}, {pct:.0f}th percentile)" if q and pct is not None else ""
            out.append({
                "label": f"CiteScore {cs['year']} (not SJR)" if cs.get("year") else "CiteScore (not SJR)",
                "value": f"{cs['value']:.1f}{extra}",
                "tone": {"Q1": "good", "Q2": "good", "Q3": "warn", "Q4": "bad"}.get(q, "neutral"),
                "source": "scopus",
            })
        if d.get("sjr") and sj["status"] != FOUND:   # SCImago's tile above already shows the SJR
            out.append({"label": f"SJR {d['sjr']['year']}", "value": f"{d['sjr']['value']:.3f}", "tone": "neutral", "source": "scopus"})
    elif sc["status"] == "not_found" and not report.get("candidates"):
        indexed = {"label": "Indexed in Scopus", "value": "Not found", "tone": "bad", "source": "scopus"}

    c = s["crest"]
    if c["status"] == FOUND:
        out.append({"label": "DHET subsidy", "value": c["data"]["status"], "tone": "good" if c["data"]["eligible"] else "bad", "source": "crest"})
    elif c["status"] == "not_found":
        out.append({"label": "DHET subsidy", "value": "Not on approved list", "tone": "bad", "source": "crest"})

    k = s["kanalregister"]
    if k["status"] == FOUND:
        lvl = k["data"].get("level")
        tone = {"2": "good", "1": "good", "0": "bad", "X": "warn"}.get(lvl or "", "neutral")
        if k["data"].get("ceased"):
            tone = "bad"
        value = f"Level {lvl}" if lvl else "Listed"
        if k["data"].get("ceased"):
            value += f" (ceased {k['data']['ceased']})"
        out.append({"label": "Norwegian Register", "value": value, "tone": tone, "source": "kanalregister"})
    elif k["status"] == "not_found":
        out.append({"label": "Norwegian Register", "value": "Not listed", "tone": "warn", "source": "kanalregister"})

    n = s["sanlic"]
    if n["status"] == FOUND:
        d = n["data"]
        value = ("Covered" if d["included"] else "Not covered") + (f" ({d['oa_type']})" if d.get("oa_type") else "")
        out.append({"label": "SANLiC APC agreement", "value": value, "tone": "good" if d["included"] else "warn", "source": "sanlic"})
    elif n["status"] == "not_found":
        out.append({"label": "SANLiC APC agreement", "value": "Not covered", "tone": "warn", "source": "sanlic"})

    tile = apc_tile(report)
    if tile:
        out.append(tile)

    j = s["journal_site"]
    for key in ("first_decision", "submission_to_acceptance", "acceptance_rate", "impact_factor"):
        m = j["data"].get("metrics", {}).get(key)
        if m:
            out.append({"label": m["label"], "value": m["value"], "tone": "neutral", "source": "journal_site"})
    if indexed:
        out.append(indexed)
    isr = report["sources"].get("issn") or {}
    if isr.get("status") == "not_found":   # only shown when something is wrong with the ISSN itself
        bad = (isr.get("data") or {}).get("invalid")
        out.insert(0, {"label": "ISSN", "value": "Not a valid ISSN (typo?)" if bad else "No record in the ISSN register",
                       "tone": "bad" if bad else "warn", "source": "issn"})
    return out


def _price(p: dict) -> str:
    return f"{p.get('currency', '')} {p['price']:,}".strip() if isinstance(p.get("price"), (int, float)) else ""


def apc_tile(report: dict) -> dict | None:
    """One APC figure: the journal's own statement first, then DOAJ, then OpenAlex's list price."""
    s = report["sources"]
    site = ((s.get("journal_site") or {}).get("data") or {}).get("metrics", {}).get("apc")
    a = (s.get("apc") or {}).get("data") or {}
    sanl = s.get("sanlic") or {}
    covered = sanl.get("status") == FOUND and sanl["data"].get("included")
    value = source = None
    if site:
        value, source = site["value"], "journal site"
    elif a.get("doaj"):
        d = a["doaj"]
        usd = next((p for p in d["prices"] if p.get("currency") == "USD"), None) or (d["prices"] or [None])[0]
        value = "No APC" if d.get("has_apc") is False else (_price(usd) if usd else None)
        source = "DOAJ"
    if not value and a.get("openalex") and a["openalex"].get("apc_usd"):
        value, source = f"USD {a['openalex']['apc_usd']:,} (list price)", "OpenAlex"
    if not value:
        return None
    if covered and value != "No APC":
        value += " · SANLiC covered"
    return {"label": "APC", "value": value, "tone": "good" if covered or value == "No APC" else "neutral",
            "source": source.replace(" ", "_") if source == "journal site" else source.lower()}


# Longest wait per source before it is reported as not answering (seconds).
LIMITS = {"scopus": 30, "crest": 30, "sanlic": 90, "apc": 25, "kanalregister": 60, "journal_site": 75, "guidelines": 60, "sjr": 120, "issn": 20}
NAMES = {"scopus": scopus.NAME, "crest": crest.NAME, "sanlic": sanlic.NAME, "apc": apc.NAME,
         "kanalregister": kanalregister.NAME, "journal_site": journal_site.NAME,
         "guidelines": guidelines.NAME, "sjr": sjr.NAME, "issn": issn_portal.NAME}


async def _timed(key: str, coro, timings: dict, progress):
    """Run one source with a time limit, recording how long it took."""
    start = time.monotonic()
    progress(key, "working", None)
    try:
        result = await asyncio.wait_for(coro, LIMITS[key])
    except asyncio.TimeoutError:
        result = source_result(NAMES[key], ERROR, error=f"No answer within {LIMITS[key]} seconds; try again later.")
    timings[key] = round(time.monotonic() - start, 1)
    res = result[0] if isinstance(result, tuple) else result
    progress(key, res["status"], res)
    return result


def partial_report(query: str, results: dict) -> dict:
    """A report from the sources finished so far; the rest show as 'checking'."""
    sources = {k: results.get(k) or {"source": NAMES[k], "status": "working", "data": {}} for k in NAMES}
    sd = sources["scopus"]["data"] if sources["scopus"]["status"] == FOUND else {}
    kd = sources["kanalregister"]["data"] if sources["kanalregister"]["status"] == FOUND else {}
    report = {"query": query, "candidates": [], "partial": True, "sources": sources,
              "identity": {"title": sd.get("title") or kd.get("title") or query,
                           "issns": sd.get("issns") or kd.get("issns") or [],
                           "publisher": sd.get("publisher") or kd.get("publisher"), "country": kd.get("country"),
                           "homepage": sd.get("homepage") or kd.get("homepage")}}
    report["identity"]["title"] = report_title(report)
    report["glance"] = _glance(report)
    return report


async def build_report(
    fetcher: Fetcher,
    query: str,
    source_id: str | None = None,
    site_url: str | None = None,
    scopus_key: str | None = None,
    scopus_token: str | None = None,
    progress=lambda key, state, result=None: None,
    openalex_key: str | None = None,
    sjr_list=None,
) -> dict:
    query = (query or "").strip()
    issn = normalise_issn(query) if looks_like_issn(query) else None
    timings: dict[str, float] = {}
    # With an ISSN, Kanalregister (the slowest source) can start straight away, alongside Scopus.
    early_kanal = (asyncio.ensure_future(_timed("kanalregister", kanalregister.lookup(fetcher, None, [issn]), timings, progress))
                   if issn and not source_id else None)
    # The ISSN register gives the title and the journal's other ISSN (print <-> online) within a second or two,
    # so the lists that know only one of the two still match, and CREST need not wait for Kanalregister's title.
    issn_task = (asyncio.ensure_future(_timed("issn", issn_portal.lookup(fetcher, issn), timings, progress))
                 if issn and not source_id else None)
    await asyncio.sleep(0)   # let them start before Scopus

    res = await _timed("scopus", scopus.lookup(fetcher, query, issn, source_id, key=scopus_key, token=scopus_token),
                       timings, progress)
    sj, candidates = res if isinstance(res, tuple) else (res, [])
    portal = await issn_task if issn_task else None
    pd = portal["data"] if portal and portal["status"] == FOUND else {}
    if sj["status"] == "not_found" and pd and not candidates:
        for other in issn_portal.all_issns(pd)[1:]:   # Scopus may hold the journal under its other ISSN
            res2 = await scopus.lookup(fetcher, other, other, None, key=scopus_key, token=scopus_token)
            if (res2[0] if isinstance(res2, tuple) else res2)["status"] == FOUND:
                sj = res2[0] if isinstance(res2, tuple) else res2
                break
    report: dict = {"query": query, "candidates": candidates, "sources": {"scopus": sj}, "timings": timings}

    sjd = sj["data"] if sj["status"] == FOUND else {}
    title = sjd.get("title") or (None if issn else query or None)
    search_title = title or pd.get("title")
    issns = list(sjd.get("issns") or ([issn] if issn else []))
    if issn and issn not in issns:
        issns.insert(0, issn)
    for i in issn_portal.all_issns(pd) if pd else []:
        if i not in issns:
            issns.append(i)

    if candidates:  # ambiguous title: let the user choose before querying the rest
        if early_kanal:
            early_kanal.cancel()
        for key in ("crest", "kanalregister", "sanlic", "journal_site", "apc", "guidelines", "sjr", "issn"):
            report["sources"][key] = {"source": key, "status": "skipped", "data": {}, "note": "Choose a journal first."}
        report["identity"] = {"title": None, "issns": [], "publisher": None}
        report["glance"] = []
        return report

    homepage = site_url or sjd.get("homepage") or pd.get("homepage")

    async def homepage_after_kanal(kanal_task):
        if homepage:
            return homepage
        k = await kanal_task
        return k["data"].get("homepage") if k["status"] == FOUND else None

    async def site_after_kanal(kanal_task):
        url = await homepage_after_kanal(kanal_task)
        return await _timed("journal_site", journal_site.lookup(fetcher, url), timings, progress)

    async def guidelines_after_kanal(kanal_task):
        url = await homepage_after_kanal(kanal_task)
        return await _timed("guidelines", guidelines.lookup(fetcher, url), timings, progress)

    kanal_task = early_kanal or asyncio.ensure_future(
        _timed("kanalregister", kanalregister.lookup(fetcher, title, issns), timings, progress))
    if not search_title:   # CREST searches by title only: get it from Kanalregister first
        k = await kanal_task
        if k["status"] == FOUND and k["data"].get("title"):
            title = search_title = k["data"]["title"]
    async def apc_when_ready():
        ids = list(issns)
        if not ids:   # title search without Scopus: Kanalregister may know the ISSN
            k = await kanal_task
            ids = k["data"].get("issns", []) if k["status"] == FOUND else []
        return await _timed("apc", apc.lookup(fetcher, ids, openalex_key), timings, progress)

    async def sjr_when_ready():
        ids = list(issns)
        if not ids:   # title search without Scopus: Kanalregister may know the ISSN
            k = await kanal_task
            ids = k["data"].get("issns", []) if k["status"] == FOUND else []
        return await _timed("sjr", sjr.lookup(sjr_list, ids, search_title), timings, progress)

    async def issn_record():
        if portal is not None:
            return portal
        ids = list(issns)
        if not ids:
            k = await kanal_task
            ids = k["data"].get("issns", []) if k["status"] == FOUND else []
        return await _timed("issn", issn_portal.lookup(fetcher, ids[0] if ids else None), timings, progress)

    crest_r, sanlic_r, site_r, apc_r, guide_r, sjr_r, issn_r = await asyncio.gather(
        _timed("crest", crest.lookup(fetcher, search_title, issns), timings, progress),
        _timed("sanlic", sanlic.lookup(fetcher, search_title, issns), timings, progress),
        site_after_kanal(kanal_task),
        apc_when_ready(),
        guidelines_after_kanal(kanal_task),
        sjr_when_ready(),
        issn_record(),
    )
    kanal_r = await kanal_task

    # Fill identity gaps from Kanalregister when Scopus has nothing.
    kd = kanal_r["data"] if kanal_r["status"] == FOUND else {}
    title = title or kd.get("title") or (crest_r["data"].get("title") if crest_r["status"] == FOUND else None) or search_title
    for i in kd.get("issns", []):
        if i not in issns:
            issns.append(i)

    report["sources"].update(
        {"crest": crest_r, "kanalregister": kanal_r, "sanlic": sanlic_r, "journal_site": site_r, "apc": apc_r,
         "guidelines": guide_r, "sjr": sjr_r, "issn": issn_r}
    )
    report["identity"] = {
        "title": title,
        "issns": issns,
        "publisher": sjd.get("publisher") or kd.get("publisher") or crest_r["data"].get("publisher"),
        "country": sjd.get("country") or kd.get("country") or pd.get("country"),
        "homepage": homepage or kd.get("homepage"),
    }
    report["identity"]["title"] = report_title(report)   # the journal's name, not the ISSN searched
    report["glance"] = _glance(report)
    return report
