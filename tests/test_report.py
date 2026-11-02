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

    async def get(self, url, headers=None):
        class R:
            pass
        r = R()
        assert headers and headers.get("X-ELS-APIKey") == "test-key"
        if "/issn/1532-0464" in url or "/issn/1532-0480" in url:
            r.status_code, r.text = 200, (FIX / "scopus_issn.json").read_text()
        elif "title=" in url:
            r.status_code, r.text = 200, (FIX / "scopus_search.json").read_text()
        else:
            r.status_code, r.text = 404, '{"service-error":{"status":{"statusCode":"RESOURCE_NOT_FOUND"}}}'
        return r

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
    r = asyncio.run(build_report(fetcher, "1532-0464", scopus_key="test-key"))
    s = r["sources"]
    assert {k: v["status"] for k, v in s.items()} == {
        "scopus": "found", "crest": "found", "kanalregister": "found", "sanlic": "found", "journal_site": "found",
    }
    assert r["identity"]["title"] == "Journal of Biomedical Informatics"
    assert r["identity"]["issns"] == ["1532-0464", "1532-0480"]
    assert s["sanlic"]["data"]["agreement_period"] == "2026-2028"
    assert s["journal_site"]["data"]["metrics"]["apc"]["value"] == "USD 3,550"
    assert s["scopus"]["data"]["best_quartile"] == "Q1"
    labels = [g["label"] for g in r["glance"]]
    assert "CiteScore 2025" in labels
    cs = next(g for g in r["glance"] if g["label"] == "CiteScore 2025")
    assert cs["value"] == "10.8 (Q1, 95th percentile)"
    assert "DHET subsidy" in labels and "Norwegian Register" in labels and "SANLiC APC agreement" in labels
    assert not r["candidates"]


def test_report_without_api_key_still_runs_other_sources(tmp_path):
    fetcher = FakeFetcher(make_xlsx(tmp_path / "l.xlsx"))
    r = asyncio.run(build_report(fetcher, "1532-0464"))
    assert r["sources"]["scopus"]["status"] == "skipped" and "API key" in r["sources"]["scopus"]["note"]
    assert r["sources"]["crest"]["status"] == "found"


def test_title_with_several_matches_asks_to_choose(tmp_path):
    fetcher = FakeFetcher(make_xlsx(tmp_path / "l.xlsx"))
    r = asyncio.run(build_report(fetcher, "Biomedical", scopus_key="test-key"))
    assert [c["source_id"] for c in r["candidates"]] == ["1532-0464", "2168-2194", "2590-177X"]
    r2 = asyncio.run(build_report(fetcher, "Biomedical", source_id="1532-0464", scopus_key="test-key"))
    assert r2["identity"]["title"] == "Journal of Biomedical Informatics" and not r2["candidates"]


def test_scopus_falls_back_when_citescore_view_is_refused(tmp_path):
    fetcher = FakeFetcher(make_xlsx(tmp_path / "l.xlsx"))
    seen = []
    real_get = fetcher.get

    async def get(url, headers=None):
        seen.append(url.rsplit("view=", 1)[-1])
        r = await real_get(url, headers)
        if "view=CITESCORE" in url:
            r.status_code, r.text = 400, '{"service-error":{"status":{"statusCode":"INVALID_INPUT","statusText":"View not allowed"}}}'
        return r

    fetcher.get = get
    r = asyncio.run(build_report(fetcher, "1532-0464", scopus_key="test-key"))
    sc = r["sources"]["scopus"]
    assert seen[:2] == ["CITESCORE", "ENHANCED"] and sc["status"] == "found"
    assert sc["data"]["subject_ranks"] and sc["data"]["ranks_unavailable"] is False and sc["note"] is None


def test_hanging_source_times_out_without_blocking_the_report(tmp_path, monkeypatch):
    import app.report as rep
    from app.sources import kanalregister

    async def hang(*a, **k):
        await asyncio.sleep(60)

    monkeypatch.setattr(kanalregister, "lookup", hang)
    monkeypatch.setitem(rep.LIMITS, "kanalregister", 0.3)
    seen = {}
    fetcher = FakeFetcher(make_xlsx(tmp_path / "l.xlsx"))
    r = asyncio.run(build_report(fetcher, "1532-0464", scopus_key="test-key", progress=lambda k, s, res=None: seen.__setitem__(k, s)))
    k = r["sources"]["kanalregister"]
    assert k["status"] == "error" and "No answer within 0.3 seconds" in k["error"]
    assert r["sources"]["crest"]["status"] == "found" and r["sources"]["journal_site"]["status"] == "found"
    assert set(r["timings"]) == {"scopus", "crest", "sanlic", "kanalregister", "journal_site"}
    assert seen["kanalregister"] == "error" and seen["scopus"] == "found"


def test_partial_report_and_kanalregister_shortcuts(tmp_path, monkeypatch):
    import app.report as rep
    from app.sources import kanalregister

    monkeypatch.setattr(kanalregister, "_ID_CACHE", tmp_path / "ids.json")
    fetcher = FakeFetcher(make_xlsx(tmp_path / "l.xlsx"))
    rendered = []
    real_render = fetcher.render

    async def render(url, **kw):
        rendered.append(url)
        return await real_render(url)

    fetcher.render = render
    order = []
    asyncio.run(build_report(fetcher, "1532-0464", scopus_key="test-key",
                             progress=lambda k, s, res=None: order.append((k, s))))
    # Kanalregister starts before Scopus has finished
    assert order.index(("kanalregister", "working")) < order.index(("scopus", "found"))
    assert any("/sok" in u for u in rendered)

    # Second lookup: the Kanalregister entry is remembered, no search page
    rendered.clear()
    r = asyncio.run(build_report(fetcher, "1532-0480", scopus_key="test-key"))
    assert r["sources"]["kanalregister"]["status"] == "found"
    assert not any("/sok" in u for u in rendered)

    # A partial report shows finished sources and 'working' for the rest
    p = rep.partial_report("1532-0464", {"scopus": r["sources"]["scopus"]})
    assert p["sources"]["crest"]["status"] == "working" and p["identity"]["title"] == "Journal of Biomedical Informatics"
    assert [g["label"] for g in p["glance"]][:2] == ["Indexed in Scopus", "CiteScore 2025"]
