"""The journal's author guidelines ("Guide for authors", "Submission guidelines").

Used for: word, page and character limits for submissions, abstract length, and
caps on figures, tables and references. Guidelines are prose and differ by
article type, so the sentences that state a limit are returned as written,
each with the page it was read from, rather than reduced to one number.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from ..fetch import FetchError, Fetcher
from ..util import clean_text
from . import ERROR, FOUND, NOT_FOUND, SKIPPED, source_result

NAME = "Author guidelines"
MAX_LIMITS = 14

NUM = r"\d{1,3}(?:[,\s]\d{3})*|\d+"
RANGE = rf"(?:{NUM})(?:\s*(?:-|–|—|to)\s*(?:{NUM}))?"
LIMIT_WORDS = re.compile(
    r"\b(?:limit(?:ed)?|maximum|max\.?|not (?:to )?exceed|exceed(?:ing)?|up to|no (?:more|longer) than|"
    r"at most|should (?:be|not)|must (?:be|not)|between|approximately|around|about|length|long|"
    r"recommended|range|typically|word count|page count|allowed)\b", re.I)

# kind -> (label, value pattern). Order matters: the first kind that matches a sentence wins.
KINDS: list[tuple[str, str, re.Pattern]] = [
    ("abstract", "Abstract", re.compile(rf"\babstracts?\b[^.]{{0,80}}?\b({RANGE})\s*words?\b|\b({RANGE})[\s-]*words?\b[^.]{{0,40}}\babstracts?\b", re.I)),
    ("words", "Word limit", re.compile(rf"\b({RANGE})\s*(?:-\s*)?words?\b", re.I)),
    ("pages", "Page limit", re.compile(
        rf"\b({RANGE})\s*(?:-\s*)?(?:(?:printed|journal|manuscript|typeset|double[- ]spaced|single[- ]spaced|A4|standard)\s+)*pages?\b", re.I)),
    ("characters", "Character limit", re.compile(rf"\b({RANGE})\s*characters?\b", re.I)),
    ("items", "Figures, tables and references", re.compile(
        rf"\b(?:up to|maximum (?:of )?|max\.?|no more than|not (?:to )?exceed|limit(?:ed)? to|at most)\s*({NUM})\s*"
        r"(?:\w+\s+){0,2}(?:figures?|tables?|illustrations?|display items|references|citations)\b", re.I)),
]

LINK_TEXT = re.compile(
    r"guide for authors|authors?'? guidelines?|instructions (?:for|to) authors|author instructions|"
    r"submission guidelines|information for authors|preparing your (?:manuscript|paper|article)|"
    r"manuscript preparation|for authors", re.I)


def guidelines_url(homepage: str) -> str | None:
    """The author-guidelines page for the main publishers (same rules as the report's Templates links)."""
    try:
        u = urlparse(homepage)
    except ValueError:
        return None
    host, seg = u.netloc.lower(), [s for s in u.path.split("/") if s]

    def after(name):
        return seg[seg.index(name) + 1] if name in seg and seg.index(name) + 1 < len(seg) else None

    if host.endswith("sciencedirect.com") and after("journal"):
        return f"https://www.sciencedirect.com/journal/{after('journal')}/publish/guide-for-authors"
    if host.endswith("journals.elsevier.com") and seg:
        return f"https://www.sciencedirect.com/journal/{seg[0]}/publish/guide-for-authors"
    if host.endswith("springer.com"):
        m = re.search(r"/journal/(\d+)", u.path)
        if m:
            return f"https://link.springer.com/journal/{m.group(1)}/submission-guidelines"
    if host.endswith("mdpi.com") and after("journal"):
        return f"https://www.mdpi.com/journal/{after('journal')}/instructions"
    if host.endswith("tandfonline.com"):
        code = after("journals") or after("toc") or after("loi") or (parse_qs(u.query).get("journalCode") or [None])[0]
        if code:
            return f"https://www.tandfonline.com/action/authorSubmission?show=instructions&journalCode={code}"
    if host.endswith("frontiersin.org") and after("journals"):
        return f"https://www.frontiersin.org/journals/{after('journals')}/for-authors/author-guidelines"
    if host.endswith("sagepub.com") and after("home"):
        return f"https://journals.sagepub.com/author-instructions/{after('home').upper()}"
    if host.endswith("onlinelibrary.wiley.com") and after("journal"):
        return f"https://onlinelibrary.wiley.com/page/journal/{after('journal')}/homepage/forauthors.html"
    return None


def guideline_links(html: str, base: str) -> list[str]:
    """Links on a journal homepage that lead to its author guidelines, best first."""
    soup = BeautifulSoup(html, "html.parser")
    scored = []
    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" ")) or a.get("title") or ""
        if len(text) > 80 or not LINK_TEXT.search(text):
            continue
        href = urljoin(base, a["href"])
        if not href.startswith("http"):
            continue
        rank = 0 if re.search(r"guide|instruction|guidelines", text, re.I) else 1
        scored.append((rank, href))
    out = []
    for _, href in sorted(scored, key=lambda x: x[0]):
        if href not in out:
            out.append(href)
    return out[:2]


def _sentences(html: str) -> list[tuple[str, str]]:
    """(sentence, nearest heading) pairs from the page's readable text."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "head", "nav", "footer", "form"]):
        t.decompose()
    out, heading = [], ""
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "p", "li", "td", "dd", "dt", "th"]):
        if el.name.startswith("h"):
            heading = clean_text(el.get_text(" "))[:80]
            continue
        if el.find(["p", "li", "td", "dd"]):   # read the innermost blocks only
            continue
        text = clean_text(el.get_text(" "))
        if not text:
            continue
        for s in re.split(r"(?<=[.;!?])\s+(?=[A-Z(])", text):
            s = s.strip()
            if 12 <= len(s):
                out.append((s, heading))
    return out


def extract_limits(html: str, url: str) -> list[dict]:
    found, seen = [], set()
    for sentence, heading in _sentences(html):
        for kind, label, pat in KINDS:
            m = pat.search(sentence)
            if not m:
                continue
            value = clean_text(next(g for g in m.groups() if g) if m.groups() else m.group(0))
            if kind != "items" and not LIMIT_WORDS.search(sentence):
                break
            if kind in ("words", "abstract") and re.fullmatch(r"\d", value):   # "two words", "1 word"
                break
            text = sentence if len(sentence) <= 280 else _around(sentence, m.start(), m.end())
            key = re.sub(r"\W+", " ", text.lower())
            if key in seen:
                break
            seen.add(key)
            unit = {"abstract": "words", "words": "words", "pages": "pages", "characters": "characters"}.get(kind, "")
            found.append({"kind": kind, "label": label, "value": f"{value} {unit}".strip() if unit else m.group(0).strip(),
                          "text": text, "section": heading, "url": url})
            break
    return found


def _around(sentence: str, start: int, end: int) -> str:
    a, b = max(0, start - 140), min(len(sentence), end + 120)
    return ("…" if a else "") + sentence[a:b].strip() + ("…" if b < len(sentence) else "")


async def lookup(fetcher: Fetcher, homepage: str | None) -> dict:
    if not homepage:
        return source_result(NAME, SKIPPED, note="No journal homepage was found, so the author guidelines could not be read.")
    resolve = getattr(fetcher, "resolve", None)
    landed = await resolve(homepage) if resolve else homepage
    pages = [u for u in [guidelines_url(landed)] if u]
    errors, checked, limits = [], [], []
    if not pages:   # unknown publisher: follow the homepage's "Guide for authors" link
        try:
            html, _ = await fetcher.page_html(landed)
            pages = guideline_links(html, landed)
        except FetchError as exc:
            errors.append(f"{landed}: {exc}")
    for url in pages:
        try:
            html, method = await fetcher.page_html(url)
            got = extract_limits(html, url)
            if not got and method == "http" and fetcher.use_browser:   # filled in by JavaScript
                html = await fetcher.render(url)
                got = extract_limits(html, url)
            checked.append(url)
            limits.extend(got)
        except FetchError as exc:
            errors.append(f"{url}: {exc}")
        if limits:
            break
    data = {"homepage": homepage, "pages_checked": checked, "limits": limits[:MAX_LIMITS],
            "guidelines_url": checked[0] if checked else (pages[0] if pages else None)}
    if not checked:
        if not pages and not errors:
            return source_result(NAME, NOT_FOUND, homepage, data,
                                 note="No author-guidelines page was found from the journal's homepage. Check its 'Guide for authors'.")
        return source_result(NAME, ERROR, homepage, data, error="; ".join(errors) or "The guidelines page could not be read.")
    if not limits:
        return source_result(NAME, NOT_FOUND, checked[0], data,
                             note="The author guidelines were read but state no word, page or figure limits.")
    return source_result(NAME, FOUND, checked[0], data, note="; ".join(errors) if errors else None)
