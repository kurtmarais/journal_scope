"""The journal's own website.

Used for: the statistics publishers post about their journals -- days to
first decision / acceptance / publication, acceptance rate, APC, and the
impact factor the journal reports.

Publishers lay these out differently, so values are found by their labels
("Submission to first decision", "Acceptance rate", "APC", ...). Every value
is returned with the text it was read from and the page URL, so it can be
checked at a glance.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString

from ..fetch import FetchError, Fetcher
from ..util import clean_text
from . import ERROR, FOUND, NOT_FOUND, SKIPPED, source_result

NAME = "Journal website"

DAYS = r"(\d+(?:\.\d+)?)\s*(days?|weeks?)\b"
PERCENT = r"(\d+(?:\.\d+)?)\s*%"
CURRENCY = r"(?:USD|US\$|EUR|GBP|CHF|ZAR|AUD|CAD|\$|€|£)"
MONEY = rf"({CURRENCY}\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|CHF|ZAR))"
DECIMAL = r"\b(\d{1,3}\.\d{1,3})\b"
BARE_INT = r"\b(\d{1,4})\b"

# metric key -> (human label, [(label regex, value regex), ...]) in priority order.
METRICS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "impact_factor": ("Impact factor (as reported by the journal)", [
        (r"(?<!5-year )(?<!five-year )\bjournal impact factor\b", DECIMAL),
        (r"(?<!5-year )(?<!five-year )\bimpact factor\b", DECIMAL),
    ]),
    "first_decision": ("Submission to first decision", [
        (r"submission to first (?:editorial )?decision \((?:median )?days\)", BARE_INT),
        (r"submission to first (?:editorial )?decision", DAYS),
        (r"(?:time|days) to first (?:editorial )?decision", DAYS),
        (r"first decision time", DAYS),
        (r"\bfirst decision\b", DAYS),
    ]),
    "decision_after_review": ("Submission to decision after review", [
        (r"submission to (?:first )?post-review decision", DAYS),
        (r"submission to decision after review", DAYS),
        (r"decision after (?:peer )?review", DAYS),
        (r"(?:peer )?review time", DAYS),
    ]),
    "submission_to_acceptance": ("Submission to acceptance", [
        (r"median days from submission to acceptance", BARE_INT),
        (r"submission to acceptance \((?:median )?days\)", BARE_INT),
        (r"submission to acceptance", DAYS),
        (r"time to acceptance", DAYS),
    ]),
    "acceptance_to_publication": ("Acceptance to publication", [
        (r"acceptance to (?:online |final )?publication", DAYS),
    ]),
    "acceptance_rate": ("Acceptance rate", [
        (r"(?:article )?acceptance rate", PERCENT),
    ]),
    "apc": ("Article processing charge (APC)", [
        (r"article (?:publishing|processing) charges?", MONEY),
        (r"\bAPCs?\b", MONEY),
        (r"publication fees?", MONEY),
        (r"open access fees?", MONEY),
    ]),
}


def candidate_urls(homepage: str) -> list[str]:
    """Pages on the publisher's site that usually carry the journal's statistics."""
    u = urlparse(homepage)
    host, path = u.netloc.lower(), u.path
    seg = [s for s in path.split("/") if s]

    if "journals.elsevier.com" in host and seg:
        slug = seg[0]
        return [f"https://www.sciencedirect.com/journal/{slug}/about/insights",
                f"https://www.sciencedirect.com/journal/{slug}"]
    if "sciencedirect.com" in host and "journal" in seg:
        slug = seg[seg.index("journal") + 1]
        return [f"https://www.sciencedirect.com/journal/{slug}/about/insights",
                f"https://www.sciencedirect.com/journal/{slug}"]
    if "springer.com" in host or "springeropen.com" in host:
        m = re.search(r"/journal/(\d+)", path)
        if m:
            return [f"https://link.springer.com/journal/{m.group(1)}"]
    if host.endswith("mdpi.com") and "journal" in seg:
        base = f"https://www.mdpi.com/journal/{seg[seg.index('journal') + 1]}"
        return [base, f"{base}/apc"]
    if "tandfonline.com" in host:
        m = re.search(r"/(?:journals|toc|loi|action/journalInformation)/?(\w+)?", path)
        code = m.group(1) if m and m.group(1) else None
        if code is None:
            q = re.search(r"journalCode=(\w+)", u.query)
            code = q.group(1) if q else None
        if code:
            return [f"https://www.tandfonline.com/journals/{code}/about-this-journal",
                    f"https://www.tandfonline.com/journals/{code}"]
    if "frontiersin.org" in host and "journals" in seg:
        j = seg[seg.index("journals") + 1]
        return [f"https://www.frontiersin.org/journals/{j}",
                f"https://www.frontiersin.org/journals/{j}/for-authors/publishing-fees"]
    if host.endswith("biomedcentral.com"):
        base = f"{u.scheme}://{u.netloc}"
        return [base, f"{base}/about", f"{base}/submission-guidelines/fees-and-funding"]
    if "onlinelibrary.wiley.com" in host:
        m = re.search(r"/journal/([\dXx]{8}|\d{4}-\d{3}[\dXx])", path)
        if m:
            return [f"https://onlinelibrary.wiley.com/journal/{m.group(1)}", homepage]
    return [homepage]


def _find_value(container_text: str, label_re: re.Pattern, value_re: re.Pattern) -> tuple[str, str] | None:
    """Choose the value that belongs to the label inside `container_text`."""
    lm = label_re.search(container_text)
    values = list(value_re.finditer(container_text))
    if not values:
        return None
    if lm is None:
        v = values[0]
    elif len(values) == 1:
        v = values[0]
    else:
        after = [v for v in values if v.start() >= lm.end() and v.start() - lm.end() <= 40]
        before = [v for v in values if v.end() <= lm.start() and lm.start() - v.end() <= 40]
        v = after[0] if after else (before[-1] if before else None)
        if v is None:
            return None
    return v.group(0), container_text


def extract_metrics(html: str, url: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "head"]):
        t.decompose()
    found: dict[str, dict] = {}
    for key, (label, rules) in METRICS.items():
        for label_pat, value_pat in rules:
            label_re = re.compile(label_pat, re.I)
            value_re = re.compile(value_pat, re.I)
            hit = None
            for node in soup.find_all(string=label_re):
                if not isinstance(node, NavigableString):
                    continue
                own = clean_text(str(node))
                if len(own) > 160:
                    # Prose: look for the value just after the label in the same sentence.
                    lm = label_re.search(own)
                    vm = value_re.search(own, lm.end()) if lm else None
                    if vm and vm.start() - lm.end() <= 80:
                        hit = (vm.group(0), own[max(0, lm.start() - 40): vm.end() + 40])
                        break
                    continue
                el = node.parent
                for _ in range(4):
                    if el is None:
                        break
                    text = clean_text(el.get_text(" "))
                    if len(text) > 400:
                        break
                    res = _find_value(text, label_re, value_re)
                    if res:
                        hit = res
                        break
                    el = el.parent
                if hit:
                    break
            if hit:
                value, context = hit
                found[key] = {"label": label, "value": clean_text(value), "context": context[:300], "url": url}
                break
    return found


async def lookup(fetcher: Fetcher, homepage: str | None) -> dict:
    if not homepage:
        return source_result(
            NAME, SKIPPED,
            note="No journal homepage was found in SCImago or Kanalregister. Enter the journal URL to check it.",
        )
    pages, errors, metrics = [], [], {}
    # Many homepages redirect (old Elsevier addresses -> ScienceDirect, BMC -> Springer Nature Link):
    # pick the statistics pages from where the homepage actually lands.
    resolve = getattr(fetcher, "resolve", None)
    landed = await resolve(homepage) if resolve else homepage
    for url in candidate_urls(landed):
        try:
            html, method = await fetcher.page_html(url)
            got = extract_metrics(html, url)
            if not got and method == "http" and fetcher.use_browser:
                # Content may be filled in by JavaScript.
                html = await fetcher.render(url)
                got = extract_metrics(html, url)
            pages.append(url)
            for k, v in got.items():
                metrics.setdefault(k, v)
        except FetchError as exc:
            errors.append(f"{url}: {exc}")
    ordered = {k: metrics[k] for k in METRICS if k in metrics}
    data = {"homepage": homepage, "pages_checked": pages, "metrics": ordered}
    if not pages:
        return source_result(NAME, ERROR, homepage, data, error="; ".join(errors))
    if not ordered:
        return source_result(
            NAME, NOT_FOUND, homepage, data,
            note="The journal's pages were read but no decision times, acceptance rate or APC were stated on them.",
        )
    note = "; ".join(errors) if errors else None
    return source_result(NAME, FOUND, homepage, data, note=note)
