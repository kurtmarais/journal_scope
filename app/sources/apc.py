"""Article processing charge (APC) from OpenAlex and DOAJ, by ISSN.

OpenAlex carries publishers' list prices for many journals, including hybrid
ones; DOAJ has the APC (or "no APC") for fully open-access journals. What the
journal's own website states is added in the report (journal_site source).
"""

from __future__ import annotations

import asyncio

from .. import discover
from ..fetch import Fetcher
from . import ERROR, FOUND, NOT_FOUND, SKIPPED, source_result

NAME = "APC (OpenAlex, DOAJ)"


def parse(openalex_source: dict | None, doaj_result: dict | None) -> dict:
    data: dict = {}
    if openalex_source:
        prices = [{"price": p.get("price"), "currency": p.get("currency")}
                  for p in (openalex_source.get("apc_prices") or []) if p.get("price") is not None]
        data["openalex"] = {"apc_usd": openalex_source.get("apc_usd"), "prices": prices,
                            "is_oa": bool(openalex_source.get("is_oa")), "url": openalex_source.get("id")}
    if doaj_result:
        b = doaj_result.get("bibjson") or {}
        apc = b.get("apc") or {}
        data["doaj"] = {"has_apc": apc.get("has_apc"), "prices": apc.get("max") or [],
                        "url": apc.get("url") or (b.get("ref") or {}).get("oa_statement"),
                        "waiver": (b.get("waiver") or {}).get("has_waiver")}
    return data


async def lookup(fetcher: Fetcher, issns: list[str], openalex_key: str | None = None) -> dict:
    if not issns:
        return source_result(NAME, SKIPPED, note="No ISSN to look up the APC with.")
    oa_src, dj = None, None
    errors = []

    async def oa():
        nonlocal oa_src
        for issn in issns[:2]:
            try:
                found = (await discover.openalex(fetcher, "issn", issn, openalex_key))["sources"]
            except discover.DiscoverError as exc:
                errors.append(f"OpenAlex: {exc}")
                return
            if found:
                oa_src = found[0]
                return

    async def dj_lookup():
        nonlocal dj
        for issn in issns[:2]:
            try:
                found = (await discover.doaj(fetcher, "issn", issn))["results"]
            except discover.DiscoverError as exc:
                errors.append(f"DOAJ: {exc}")
                return
            if found:
                dj = found[0]
                return

    await asyncio.gather(oa(), dj_lookup())
    data = parse(oa_src, dj)
    if data:
        return source_result(NAME, FOUND, data=data, note="; ".join(errors) or None)
    if errors and len(errors) == 2:
        return source_result(NAME, ERROR, error="; ".join(errors))
    return source_result(NAME, NOT_FOUND, note="Neither OpenAlex nor DOAJ lists an APC for this journal.")
