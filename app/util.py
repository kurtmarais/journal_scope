"""Small helpers shared by the source modules."""

from __future__ import annotations

import re
import unicodedata

ISSN_RE = re.compile(r"\b(\d{4})[-\s]?(\d{3}[\dXx])\b")


def normalise_issn(value: str | None) -> str | None:
    """Return an ISSN as NNNN-NNNC, or None if `value` does not hold one."""
    if not value:
        return None
    m = ISSN_RE.search(value.strip())
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2).upper()}"


def find_issns(text: str | None) -> list[str]:
    """All distinct ISSNs in a string, in order of appearance.

    Handles SCImago's unhyphenated, comma-separated form ("15320464, 15320480").
    """
    if not text:
        return []
    out: list[str] = []
    for m in ISSN_RE.finditer(text):
        issn = f"{m.group(1)}-{m.group(2).upper()}"
        if issn not in out:
            out.append(issn)
    return out


def looks_like_issn(query: str) -> bool:
    return bool(re.fullmatch(r"\s*\d{4}[-\s]?\d{3}[\dXx]\s*", query or ""))


def normalise_title(value: str | None) -> str:
    """Lower-case, accent-free, punctuation-free title for comparisons.

    "Journal of Biomedical Informatics: X" -> "journal of biomedical informatics x"
    "Lancet, The" -> "lancet"
    """
    if not value:
        return ""
    v = unicodedata.normalize("NFKD", value)
    v = "".join(c for c in v if not unicodedata.combining(c)).lower()
    v = v.replace("&", " and ")
    v = re.sub(r",\s*the\s*$", "", v)
    v = re.sub(r"^the\s+", "", v)
    v = re.sub(r"[^a-z0-9]+", " ", v)
    return re.sub(r"\s+", " ", v).strip()


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def journal_key(report: dict) -> str:
    """Stable id for a journal: its first ISSN (sorted), else "t:" + normalised title."""
    ident = report.get("identity") or {}
    issns = sorted(i for i in (normalise_issn(x) for x in ident.get("issns") or []) if i)
    if issns:
        return issns[0]
    return "t:" + normalise_title(ident.get("title") or report.get("query") or "")


# Sections of a full report. Bump when a source is added, so older reports can be updated.
REPORT_SECTIONS = ("scopus", "crest", "kanalregister", "sanlic", "journal_site", "apc", "guidelines", "sjr", "issn")


def outdated(report: dict) -> bool:
    """True for a full report made before one of the current sections existed."""
    sources = (report or {}).get("sources") or {}
    return bool(sources) and not report.get("candidates") and not report.get("quick") and any(k not in sources for k in REPORT_SECTIONS)


def refresh_query(report: dict) -> str:
    """What to search for to redo a report: its first ISSN (unambiguous), else its title or query."""
    ident = report.get("identity") or {}
    return ((ident.get("issns") or [None])[0] or ident.get("title") or report.get("query") or "").strip()


_TITLE_SOURCES = ("scopus", "kanalregister", "crest", "sjr", "sanlic", "issn", "scimago")


def report_title(report: dict) -> str:
    """The journal's name for a report: never just the ISSN that was searched, when any source knows the name."""
    ident = (report or {}).get("identity") or {}
    t = ident.get("title")
    if t and not looks_like_issn(t):
        return t
    for k in _TITLE_SOURCES:
        src = ((report or {}).get("sources") or {}).get(k) or {}
        v = (src.get("data") or {}).get("title") if src.get("status") == "found" else None
        if v and not looks_like_issn(v):
            return v
    return t or (report or {}).get("query", "")
