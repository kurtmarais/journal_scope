"""End-to-end report assembly using saved pages instead of the network."""

import asyncio
from pathlib import Path

from openpyxl import Workbook

from app.fetch import FetchError
from app.report import build_report

FIX = Path(__file__).parent / "fixtures"


class FakeFetcher:
    use_browser = True

    def __init__(self, xlsx: Path):
        self.xlsx = xlsx

    def _page(self, url: str) -> str:
        if "scimagojr.com" in url:
            return (FIX / ("scimago_detail.html" if "tip=sid" in url else "scimago_search.html")).read_text()
        if "kanalregister" in url and "/sok" in url:
            return '<a href="/en/tidsskrift?id=442499"><h3>Journal of Biomedical Informatics</h3></a>'
        if "kanalregister" in url and "tidsskrift" in url:
            return (FIX / "kanalregister_journal.html").read_text()
        if "crest.sun.ac.za" in url:
            return (FIX / "crest_search.html").read_text()
        if url.startswith("https://sanlic.ac.za/read-and-publish"):
            return '<a href="https://sanlic.ac.za/sciencedirect-elsevier/">Elsevier</a>'
        if url.startswith("https://sanlic.ac.za/sciencedirect-elsevier"):
            return "<div class='entry-content'>renewed for a further three years (2026-2028).</div>"
        if "sciencedirect.com" in url and url.endswith("/about/insights"):
            return (FIX / "elsevier_insights.html").read_text()
        if "sciencedirect.com" in url:
            return "<html><body>nothing here</body></html>"
        raise FetchError(f"unexpected url {url}")

    async def page_html(self, url, wait_for=None):
        return self._page(url), "http"

    async def render(self, url, wait_for=None, wait_ms=0, settle_ms=0):
        return self._page(url)

    async def get_text(self, url, headers=None):
        return self._page(url)

    async def get_file(self, url, max_age=0):
        return self.xlsx


def make_xlsx(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(["Journal Title", "eISSN", "hybrid_gold_oa", "Included in R&P agreement",
               "Explanation if not included", "Subject", "Publisher"])
    ws.append(["Journal of Biomedical Informatics", "1532-0464", "Hybrid", "Y", None, None, "ELSEVIER"])
    wb.save(path)
    return path


def test_full_report(tmp_path):
    fetcher = FakeFetcher(make_xlsx(tmp_path / "l.xlsx"))
    r = asyncio.run(build_report(fetcher, "1532-0464"))
    s = r["sources"]
    assert {k: v["status"] for k, v in s.items()} == {
        "scimago": "found", "crest": "found", "kanalregister": "found", "sanlic": "found", "journal_site": "found",
    }
    assert r["identity"]["title"] == "Journal of Biomedical Informatics"
    assert r["identity"]["issns"] == ["1532-0464", "1532-0480"]
    assert s["sanlic"]["data"]["agreement_period"] == "2026-2028"
    assert s["journal_site"]["data"]["metrics"]["apc"]["value"] == "USD 3,550"
    labels = [g["label"] for g in r["glance"]]
    assert "DHET subsidy" in labels and "Norwegian Register" in labels and "SANLiC APC agreement" in labels
    assert not r["candidates"]
