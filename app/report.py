"""Build one journal report from all sources."""

from __future__ import annotations

import asyncio
from datetime import date

from .fetch import Fetcher
from .sources import FOUND, crest, journal_site, kanalregister, sanlic, scimago
from .util import looks_like_issn, normalise_issn


def _glance(report: dict) -> list[dict]:
    """Headline values, each taken straight from one source."""
    s = report["sources"]
    out: list[dict] = []

    sj = s["scimago"]
    if sj["status"] == FOUND:
        d = sj["data"]
        last = d.get("coverage_last_year")
        current = last is not None and last >= date.today().year - 1
        out.append({
            "label": "Indexed (Scopus via SCImago)",
            "value": f"Coverage {d.get('coverage')}" if d.get("coverage") else "Listed",
            "tone": "good" if current else "warn",
            "source": "scimago",
        })
        if d.get("sjr"):
            q = d["sjr"].get("best_quartile")
            out.append({
                "label": f"SJR {d['sjr']['year']}",
                "value": f"{d['sjr']['value']:.3f}" + (f" ({q})" if q else "") if d["sjr"].get("value") is not None else (q or "-"),
                "tone": {"Q1": "good", "Q2": "good", "Q3": "warn", "Q4": "bad"}.get(q, "neutral"),
                "source": "scimago",
            })
        if d.get("cites_per_doc_2y"):
            last_c = d["cites_per_doc_2y"][-1]
            out.append({"label": f"Cites/doc, 2 years ({last_c['year']})", "value": f"{last_c['value']:.2f}", "tone": "neutral", "source": "scimago"})
        if d.get("h_index") is not None:
            out.append({"label": "H-index", "value": str(d["h_index"]), "tone": "neutral", "source": "scimago"})
    elif sj["status"] != "error" and not report.get("candidates"):
        out.append({"label": "Indexed (Scopus via SCImago)", "value": "Not found", "tone": "bad", "source": "scimago"})

    c = s["crest"]
    if c["status"] == FOUND:
        out.append({"label": "DHET subsidy", "value": c["data"]["status"], "tone": "good" if c["data"]["eligible"] else "bad", "source": "crest"})
    elif c["status"] != "error":
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
    elif k["status"] != "error":
        out.append({"label": "Norwegian Register", "value": "Not listed", "tone": "warn", "source": "kanalregister"})

    n = s["sanlic"]
    if n["status"] == FOUND:
        d = n["data"]
        value = ("Covered" if d["included"] else "Not covered") + (f" ({d['oa_type']})" if d.get("oa_type") else "")
        out.append({"label": "SANLiC APC agreement", "value": value, "tone": "good" if d["included"] else "warn", "source": "sanlic"})
    elif n["status"] != "error":
        out.append({"label": "SANLiC APC agreement", "value": "Not covered", "tone": "warn", "source": "sanlic"})

    j = s["journal_site"]
    for key in ("apc", "first_decision", "submission_to_acceptance", "acceptance_rate", "impact_factor"):
        m = j["data"].get("metrics", {}).get(key)
        if m:
            out.append({"label": m["label"], "value": m["value"], "tone": "neutral", "source": "journal_site"})
    return out


async def build_report(
    fetcher: Fetcher,
    query: str,
    source_id: str | None = None,
    site_url: str | None = None,
) -> dict:
    query = (query or "").strip()
    issn = normalise_issn(query) if looks_like_issn(query) else None

    sj, candidates = await scimago.lookup(fetcher, query, issn, source_id)
    report: dict = {"query": query, "candidates": candidates, "sources": {"scimago": sj}}

    sjd = sj["data"] if sj["status"] == FOUND else {}
    title = sjd.get("title") or (None if issn else query or None)
    issns = list(sjd.get("issns") or ([issn] if issn else []))
    if issn and issn not in issns:
        issns.insert(0, issn)

    if candidates:  # ambiguous title: let the user choose before querying the rest
        for key in ("crest", "kanalregister", "sanlic", "journal_site"):
            report["sources"][key] = {"source": key, "status": "skipped", "data": {}, "note": "Choose a journal first."}
        report["identity"] = {"title": None, "issns": [], "publisher": None}
        report["glance"] = []
        return report

    homepage = site_url or sjd.get("homepage")

    async def site_after_kanal(kanal_task):
        url = homepage
        if not url:
            k = await kanal_task
            url = k["data"].get("homepage") if k["status"] == FOUND else None
        return await journal_site.lookup(fetcher, url)

    kanal_task = asyncio.ensure_future(kanalregister.lookup(fetcher, title, issns))
    crest_r, sanlic_r, site_r = await asyncio.gather(
        crest.lookup(fetcher, title, issns),
        sanlic.lookup(fetcher, title, issns),
        site_after_kanal(kanal_task),
    )
    kanal_r = await kanal_task

    # Fill identity gaps from Kanalregister when SCImago has nothing.
    kd = kanal_r["data"] if kanal_r["status"] == FOUND else {}
    if not title and kd.get("title"):
        title = kd["title"]
    for i in kd.get("issns", []):
        if i not in issns:
            issns.append(i)

    report["sources"].update(
        {"crest": crest_r, "kanalregister": kanal_r, "sanlic": sanlic_r, "journal_site": site_r}
    )
    report["identity"] = {
        "title": title,
        "issns": issns,
        "publisher": sjd.get("publisher") or kd.get("publisher") or crest_r["data"].get("publisher"),
        "country": sjd.get("country") or kd.get("country"),
        "homepage": homepage or kd.get("homepage"),
    }
    report["glance"] = _glance(report)
    return report
