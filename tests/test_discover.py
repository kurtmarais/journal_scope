import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from app import discover
from app.lists import LocalLists
from tests.reflist_samples import crest_xlsx, sanlic_xlsx

FIX = Path(__file__).parent / "fixtures"


class Resp:
    def __init__(self, status, body):
        self.status_code, self.text = status, json.dumps(body)


class FakeHTTP:
    """Answers like OpenAlex, DOAJ and Scopus do; records the URLs asked for."""

    def __init__(self):
        self.urls = []

    async def get(self, url, headers=None):
        self.urls.append((url, headers or {}))
        if "api.openalex.org/works" in url:
            return Resp(200, {"group_by": json.loads((FIX / "openalex_scope.json").read_text())["groups"] + [{"key": "unknown", "count": 5}]})
        if "api.openalex.org/sources" in url:
            data = json.loads((FIX / "openalex_title.json").read_text())
            return Resp(200, {"results": data["sources"]})
        if "doaj.org" in url:
            return Resp(200, json.loads((FIX / "doaj_search.json").read_text()))
        if "api.elsevier.com" in url:
            return Resp(200 if headers.get("X-ELS-APIKey") == "k" else 401, json.loads((FIX / "scopus_facets.json").read_text()))
        return Resp(404, {})


def test_openalex_title_and_scope_requests():
    f = FakeHTTP()
    r = asyncio.run(discover.openalex(f, "title", "health informatics!", key="oa-key"))
    assert r["sources"][0]["display_name"] == "Journal of Biomedical Informatics"
    q = parse_qs(urlparse(f.urls[0][0]).query)
    assert q["search"] == ["health informatics"] and q["filter"] == ["type:journal"] and q["api_key"] == ["oa-key"]

    f = FakeHTTP()
    r = asyncio.run(discover.openalex(f, "scope", "clinical decision support"))
    works = parse_qs(urlparse(f.urls[0][0]).query)
    assert works["group_by"] == ["primary_location.source.id"] and "from_publication_date:" in works["filter"][0]
    assert "api_key" not in works
    assert [g["count"] for g in r["groups"]] == [320, 280, 90]          # 'unknown' dropped
    ids = parse_qs(urlparse(f.urls[1][0]).query)["filter"][0]
    assert ids.startswith("openalex:S4210203445|")


def test_doaj_and_scopus_requests():
    f = FakeHTTP()
    asyncio.run(discover.doaj(f, "scope", "medical informatics"))
    query = unquote(urlparse(f.urls[0][0]).path.rsplit("/", 1)[-1])
    assert "bibjson.keywords:(medical AND informatics)" in query and "bibjson.subject.term:" in query
    f = FakeHTTP()
    asyncio.run(discover.doaj(f, "title", "informatics"))
    assert unquote(urlparse(f.urls[0][0]).path.rsplit("/", 1)[-1]) == "bibjson.title:(informatics)"

    f = FakeHTTP()
    out = asyncio.run(discover.scopus_scope(f, "health informatics", "k"))
    assert out["search-results"]["facet"]["category"][0]["hitCount"] == "210"
    q = parse_qs(urlparse(f.urls[0][0]).query)
    assert q["query"][0].startswith("TITLE-ABS-KEY(health informatics) AND PUBYEAR >") and "exactsrctitle" in q["facets"][0]
    with pytest.raises(discover.DiscoverError, match="institution"):
        asyncio.run(discover.scopus_scope(FakeHTTP(), "x y", "bad"))
    with pytest.raises(discover.DiscoverError, match="API key"):
        asyncio.run(discover.scopus_scope(FakeHTTP(), "x y", None))


def test_local_lists_search_and_match(tmp_path):
    crest = tmp_path / "crest.xlsx"
    sanl = tmp_path / "sanlic.xlsx"
    crest_xlsx(crest)
    sanlic_xlsx(sanl)

    class F:
        async def get_text(self, url, headers=None):
            if "crest" in url:
                return '<a href="https://db.crest.sun.ac.za/zapublications/downloadfile/x.xlsx">d</a>'
            return '<a href="https://sanlic.ac.za/wp-content/uploads/Alphabetical-RP-Journal-Title-List_2026.xlsx">x</a>'

        async def get_file(self, url, max_age=0):
            return crest if "crest" in url else sanl

    lists = LocalLists(F(), tmp_path)
    asyncio.run(lists.ensure())
    assert lists.errors == {}
    hits = lists.search_crest("biomedical informatics")
    assert [h["key"] for h in hits] == ["JNL_014249", "JNL_024590"]     # eligible first
    assert lists.search_crest("infor biomed")                              # word starts match
    assert not lists.search_crest("cardiology")
    m = lists.match(["1532-0480"], ["JMIR Medical Informatics"])
    assert [r["key"] for r in m["crest"]] == ["JNL_014249"]
    assert {r["title"] for r in m["sanlic"]} == {"JMIR Medical Informatics"}


def test_desktop_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    async def fake_openalex(fetcher, mode, q, key=None):
        return {"mode": mode, "q": q, "key": key}

    async def fake_scopus(fetcher, q, key, token=None):
        raise discover.DiscoverError("Scopus search needs your institution's access")

    async def ensure(self):
        self.crest = [{"key": "JNL_1", "title": "Journal of Biomedical Informatics", "title_norm": "journal of biomedical informatics",
                       "issn": "1532-0464", "eissn": "1532-0480", "status": "Eligible for subsidy", "publisher": "Elsevier", "reason": None, "variants": None}]
        self.sanlic = []

    monkeypatch.setattr(discover, "openalex", fake_openalex)
    monkeypatch.setattr(discover, "scopus_scope", fake_scopus)
    monkeypatch.setattr(LocalLists, "ensure", ensure)
    main.app.state.openalex_key = lambda: "oa"
    main.app.state.lists_dir = tmp_path
    main.app.state.db = None
    with TestClient(main.app) as c:
        assert c.get("/api/discover/openalex", params={"q": "informatics", "mode": "scope"}).json() == {"mode": "scope", "q": "informatics", "key": "oa"}
        r = c.get("/api/discover/scopus", params={"q": "informatics", "mode": "scope"})
        assert r.status_code == 502 and "institution" in r.json()["detail"]
        assert c.get("/api/discover/crest", params={"q": "biomedical"}).json()["results"][0]["key"] == "JNL_1"
        m = c.post("/api/refmatch", json={"issns": ["1532-0480"], "titles": []}).json()
        assert m["crest"][0]["key"] == "JNL_1" and m["sanlic"] == []
        assert c.get("/api/discover/openalex", params={"q": "x"}).status_code == 422     # too short
    main.app.state.openalex_key = lambda: None
