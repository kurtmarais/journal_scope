"""The ISSN Portal (portal.issn.org), the international ISSN register.

Used for: confirming that an ISSN exists and what it belongs to (key title,
title proper, print or online), the journal's other ISSN (print <-> online)
and ISSN-L, its country and web address. The other ISSN matters: CREST,
SANLiC, SCImago and Scopus often list only one of a journal's two ISSNs, so
a search with the other one would otherwise find nothing.

The free record page is read (the portal's data API is a paid service). Values
are taken from the page's labelled fields (data-key attributes).
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..fetch import FetchError, Fetcher, looks_blocked
from ..util import clean_text, normalise_issn
from . import ERROR, FOUND, NOT_FOUND, SKIPPED, source_result

NAME = "ISSN Portal"
HOME = "https://portal.issn.org/"
# Web addresses on ISSN records that are catalogues, not the journal itself.
_NOT_HOMEPAGES = re.compile(r"oclc\.org|worldcat\.org|issn\.org|jstor\.org/action/showPublisher|ulrichsweb", re.I)


def record_url(issn: str) -> str:
    return f"https://portal.issn.org/resource/ISSN/{issn}"


def check_digit_ok(issn: str) -> bool:
    """ISSNs end in a check digit (mod 11); a typo almost always breaks it."""
    digits = issn.replace("-", "").upper()
    if not re.fullmatch(r"\d{7}[\dX]", digits):
        return False
    total = sum(int(d) * w for d, w in zip(digits[:7], range(8, 1, -1)))
    check = (11 - total % 11) % 11
    return digits[7] == ("X" if check == 10 else str(check))


def clean_title(value: str) -> str:
    """'Journal of biomedical informatics.' -> 'Journal of biomedical informatics'."""
    v = clean_text(value)
    v = re.sub(r"\s*[./:;,=]+\s*$", "", v)
    return v[:1].upper() + v[1:] if v else v


def parse_record(html: str, issn: str) -> dict | None:
    """The record's fields, or None when the page holds no record for `issn`."""
    soup = BeautifulSoup(html, "html.parser")

    def field(key):
        el = soup.find(attrs={"data-key": key})
        return clean_text(el.get_text(" ")) if el else ""

    found = normalise_issn(field("issn"))
    if found != issn:
        return None
    issn_l = None
    a = soup.find(attrs={"data-issnl": True})
    if a:
        issn_l = normalise_issn(a["data-issnl"])
    others = []
    media = soup.find(attrs={"data-key": "other-media"})
    for link in media.find_all("a", href=True) if media else []:
        other = normalise_issn(link["href"].rsplit("/", 1)[-1])
        if other and other != issn and other not in [o["issn"] for o in others]:
            others.append({"issn": other, "medium": clean_text(link.get_text(" ")) or None})
    key_title = ""
    t = soup.select_one(".record__title .display--primary")
    if t:
        key_title = clean_text(t.get_text(" "))
    variants = [clean_title(el.get_text(" ")) for el in soup.find_all(attrs={"data-key": "title-variants"})]
    links = []
    for li in soup.find_all(attrs={"data-key": "resource-link"}):
        x = li.find("a", href=True)
        if x and x["href"] not in links:
            links.append(x["href"])
    homepage = next((u for u in links if not _NOT_HOMEPAGES.search(u)), None)
    badge = soup.find(attrs={"data-category": True})
    country = field("country")
    return {
        "issn": issn,
        "issn_l": issn_l,
        "other_issns": others,
        "key_title": key_title or None,
        "title": clean_title(field("title-proper")) or (re.sub(r"\s*\((?:Print|Online)\)\s*$", "", key_title) or None),
        "variants": [v for v in variants if v],
        "medium": field("medium") or None,
        "country": country.title() if country.isupper() else (country or None),
        "homepage": homepage,
        "links": links,
        "record_status": clean_text(badge.get_text(" ")) if badge else None,
    }


def all_issns(data: dict) -> list[str]:
    """The ISSN searched, its other medium's ISSN and the ISSN-L, without repeats."""
    out = [data["issn"]] + [o["issn"] for o in data.get("other_issns") or []] + ([data["issn_l"]] if data.get("issn_l") else [])
    return [i for n, i in enumerate(out) if i and i not in out[:n]]


async def lookup(fetcher: Fetcher, issn: str | None) -> dict:
    if not issn:
        return source_result(NAME, SKIPPED, HOME, note="No ISSN to look up.")
    url = record_url(issn)
    if not check_digit_ok(issn):
        return source_result(NAME, NOT_FOUND, url, {"issn": issn, "invalid": True},
                             note=f"{issn} is not a valid ISSN: its last digit (the check digit) doesn't match. Check for a typo.")
    try:
        resp = await fetcher.get(url)
        if resp.status_code == 404:
            html = ""
        elif resp.status_code < 400 and not looks_blocked(resp.status_code, resp.text):
            html = resp.text
        else:   # a bot check or refusal: load it in the browser like any other page
            html, _ = await fetcher.page_html(url)
    except FetchError as exc:
        return source_result(NAME, ERROR, url, error=str(exc))
    data = parse_record(html, issn)
    if data is None:
        return source_result(NAME, NOT_FOUND, url, {"issn": issn}, note="The ISSN register has no record for this ISSN.")
    return source_result(NAME, FOUND, url, data)
