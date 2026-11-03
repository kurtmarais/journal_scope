from pathlib import Path

import pytest
from openpyxl import Workbook

from app.sources import crest, journal_site, kanalregister, sanlic, scopus
from app.util import find_issns, looks_like_issn, normalise_issn, normalise_title

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- util
def test_issn_helpers():
    assert normalise_issn("15320464") == "1532-0464"
    assert normalise_issn("2590-177x") == "2590-177X"
    assert find_issns("15320464, 15320480") == ["1532-0464", "1532-0480"]
    assert looks_like_issn(" 1532-0464 ") and not looks_like_issn("Nature")
    assert normalise_title("Lancet, The") == "lancet"
    assert normalise_title("Journal of Biomedical Informatics: X") == "journal of biomedical informatics x"


# ---------------------------------------------------------------- Scopus
def test_scopus_entry():
    import json
    e = scopus.entries(json.loads(read("scopus_issn.json")))[0]
    d = scopus.parse_entry(e)
    assert d["title"] == "Journal of Biomedical Informatics" and d["source_id"] == "23706"
    assert d["issns"] == ["1532-0464", "1532-0480"]
    assert d["citescore"] == {"year": 2025, "value": 10.8}
    assert d["citescore_tracker"] == {"year": 2026, "value": 9.6}
    assert d["subject_ranks"][0] == {"subject": "Computer Science Applications", "code": "1706", "year": 2025,
                                     "rank": "40", "percentile": 95.0, "quartile": "Q1"}
    assert d["subject_ranks"][1]["quartile"] == "Q1" and d["best_quartile"] == "Q1" and d["best_percentile"] == 95.0
    assert d["sjr"] == {"year": 2025, "value": 1.32}
    assert d["coverage"] == "2001-2026" and d["coverage_last_year"] == 2026
    assert d["homepage"] == "https://www.journals.elsevier.com/journal-of-biomedical-informatics"
    assert d["ranks_unavailable"] is False


def test_scopus_quartiles_and_basic_view():
    assert [scopus.quartile(p) for p in (99, 75, 74.9, 50, 25, 24, None)] == ["Q1", "Q1", "Q2", "Q2", "Q3", "Q4", None]
    basic = {"dc:title": "T", "prism:issn": "1234-5679",
             "citeScoreYearInfoList": {"citeScoreCurrentMetric": "3.1", "citeScoreCurrentMetricYear": "2025"}}
    d = scopus.parse_entry(basic, limited=True)
    assert d["citescore"] == {"year": 2025, "value": 3.1} and d["subject_ranks"] == [] and d["ranks_unavailable"] is True


def test_scopus_search_candidates():
    import json
    cands = scopus.candidates_from(scopus.entries(json.loads(read("scopus_search.json"))))
    assert [c["source_id"] for c in cands] == ["1532-0464", "2168-2194", "2590-177X"]
    assert scopus.choose(cands, "Journal of Biomedical Informatics")["source_id"] == "1532-0464"
    assert scopus.choose(cands, "Biomedical") is None
    assert scopus.entries({"serial-metadata-response": {"entry": [{"error": "Result set was empty"}]}}) == []


# ---------------------------------------------------------------- CREST
def test_crest_rows_and_match():
    rows = crest.parse_rows(read("crest_search.html"))
    assert len(rows) == 4
    hit = crest.match(rows, ["1532-0464"], "Journal of Biomedical Informatics")
    assert hit["journal_id"] == "JNL_014249" and hit["eligible"] is True
    disc = crest.match(rows, ["2590-177X"], None)
    assert disc["status"] == "Discontinued" and disc["eligible"] is False
    assert disc["reason"] == "The journal ceased publication in 2021."
    variant = crest.match(rows, [], "Journal of Health and Biomedical Informatics")
    assert variant["journal_id"] == "JNL_035979"
    assert crest.match(rows, ["0000-0000"], "Some Other Journal") is None


# ---------------------------------------------------------------- Kanalregister
def test_kanalregister_journal():
    d = kanalregister.parse_journal(read("kanalregister_journal.html"))
    assert d["title"] == "Journal of Biomedical Informatics"
    assert d["level"] == "1"
    assert d["publisher"] == "Elsevier"
    assert d["issns"] == ["1532-0464", "1532-0480"]
    assert d["homepage"].startswith("https://www.sciencedirect.com/journal/journal-of-biomedical-informatics")
    assert d["doaj"] is False
    assert d["ceased"] is None
    assert d["level_history"][:2] == [{"year": 2026, "level": "1"}, {"year": 2025, "level": "1"}]


def test_kanalregister_search():
    html = '<a href="/tidsskrift?id=442499"><h3>Journal of Biomedical Informatics</h3></a><a href="/tidsskrift?id=442499">dup</a>'
    assert kanalregister.parse_search(html) == [{"id": "442499", "title": "Journal of Biomedical Informatics"}]


# ---------------------------------------------------------------- SANLiC
@pytest.fixture
def sanlic_xlsx(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Note: The Wiley Journal list is indicative only."])
    ws.append([])
    ws.append(["Journal Title", "eISSN", "hybrid_gold_oa", "Included in R&P agreement",
               "Explanation if not included", "Subject", "Publisher"])
    ws.append(["Journal of Biomedical Informatics", "1532-0464", "Hybrid", "Y", None, None, "ELSEVIER"])
    ws.append(["ACS Central Science", "2374-7951", "Diamond OA", "N", "Free to publish", "Chemistry", "ACS"])
    path = tmp_path / "list.xlsx"
    wb.save(path)
    return path


def test_sanlic_records(sanlic_xlsx):
    recs = sanlic.load_records(sanlic_xlsx)
    assert len(recs) == 2
    hit = sanlic.match(recs, ["1532-0480", "1532-0464"], None)
    assert hit["included"] is True and hit["oa_type"] == "Hybrid" and hit["publisher"] == "ELSEVIER"
    acs = sanlic.match(recs, [], "ACS Central Science")
    assert acs["included"] is False and acs["explanation"] == "Free to publish"


def test_sanlic_links_and_period():
    page = ('<a href="https://sanlic.ac.za/wp-content/uploads/2026/03/Alphabetical-RP-Journal-Title-List_2026.xlsx">x</a>'
            '<a href="https://sanlic.ac.za/sciencedirect-elsevier/">Elsevier</a>')
    xlsx, pages = sanlic.parse_page_links(page)
    assert xlsx.endswith("Alphabetical-RP-Journal-Title-List_2026.xlsx")
    assert sanlic.agreement_page_for("ELSEVIER", pages) == "https://sanlic.ac.za/sciencedirect-elsevier/"
    assert sanlic.agreement_page_for("ROYAL SOCIETY", []) == "https://sanlic.ac.za/royal-society/"
    period = sanlic.parse_agreement_period(
        "<div class='entry-content'>This agreement commenced in 2023 and has been renewed for a further three years (2026-2028).</div>")
    assert period == "2026-2028"


# ---------------------------------------------------------------- journal sites
def test_elsevier_metrics():
    m = journal_site.extract_metrics(read("elsevier_insights.html"), "u")
    assert m["impact_factor"]["value"] == "5.9"
    assert m["apc"]["value"] == "USD 3,550"
    assert m["first_decision"]["value"] == "2 days"
    assert m["decision_after_review"]["value"] == "49 days"
    assert m["submission_to_acceptance"]["value"] == "142 days"
    assert m["acceptance_to_publication"]["value"] == "8 days"


def test_springer_metrics():
    m = journal_site.extract_metrics(read("springer_journal.html"), "u")
    assert m["impact_factor"]["value"] == "8.8"
    assert m["first_decision"]["value"] == "8 days"
    assert m["submission_to_acceptance"]["value"] == "76"


def test_mdpi_metrics():
    m = journal_site.extract_metrics(read("mdpi_journal.html"), "u")
    assert m["impact_factor"]["value"] == "4.0"
    assert m["first_decision"]["value"] == "18 days"
    assert m["acceptance_to_publication"]["value"] == "3 days"
    assert m["apc"]["value"] == "2600 CHF"


def test_candidate_urls():
    assert journal_site.candidate_urls("https://www.journals.elsevier.com/journal-of-biomedical-informatics")[0] == \
        "https://www.sciencedirect.com/journal/journal-of-biomedical-informatics/about/insights"
    assert journal_site.candidate_urls("http://www.springer.com/medicine/journal/10916") == \
        ["https://link.springer.com/journal/10916"]
    assert journal_site.candidate_urls("https://www.mdpi.com/journal/sensors")[1] == "https://www.mdpi.com/journal/sensors/apc"
    assert journal_site.candidate_urls("https://www.tandfonline.com/toc/tjis20/current")[0] == \
        "https://www.tandfonline.com/journals/tjis20/about-this-journal"
    assert journal_site.candidate_urls("https://example.org/j") == ["https://example.org/j"]


def test_more_publisher_wordings():
    html = """<main>
      <div><span>13 days avg</span> <span>from submission to first decision</span></div>
      <div><span>81 days avg</span> <span>from submission to first post-review decision</span></div>
      <div><span>21 days avg</span> <span>from acceptance to online publication</span></div>
      <div><span>13%</span> <span>acceptance rate</span></div>
      <dl><div><dt>Submission to acceptance (median days)</dt><dd>142</dd></div></dl>
    </main>"""
    m = journal_site.extract_metrics(html, "u")
    assert m["first_decision"]["value"] == "13 days"
    assert m["decision_after_review"]["value"] == "81 days"
    assert m["acceptance_to_publication"]["value"] == "21 days"
    assert m["acceptance_rate"]["value"] == "13%"
    assert m["submission_to_acceptance"]["value"] == "142"


def test_candidates_follow_springer_link_for_bmc():
    assert journal_site.candidate_urls("https://link.springer.com/journal/12911/aims-and-scope") == \
        ["https://link.springer.com/journal/12911"]


def test_guideline_limits():
    from app.sources import guidelines
    got = guidelines.extract_limits(read("guide_for_authors.html"), "https://example.org/guide")
    by = {(g["kind"], g["value"]) for g in got}
    assert ("words", "8,000 words") in by
    assert ("words", "3000 words") in by
    assert ("words", "6000–10000 words") in by
    assert ("abstract", "250 words") in by
    assert ("pages", "25 pages") in by
    assert any(g["kind"] == "items" and "50 references" in g["value"] for g in got)
    abstract = next(g for g in got if g["kind"] == "abstract")
    assert abstract["section"] == "Abstract" and abstract["url"] == "https://example.org/guide"
    texts = " ".join(g["text"] for g in got)
    assert "12-point" not in texts and "1500 articles" not in texts and "menu" not in texts


def test_guideline_urls():
    from app.sources import guidelines
    assert guidelines.guidelines_url("https://www.sciencedirect.com/journal/journal-of-biomedical-informatics") == \
        "https://www.sciencedirect.com/journal/journal-of-biomedical-informatics/publish/guide-for-authors"
    assert guidelines.guidelines_url("https://www.mdpi.com/journal/sensors") == "https://www.mdpi.com/journal/sensors/instructions"
    assert guidelines.guidelines_url("https://example.org/j") is None
    html = '<a href="/about">About</a><a href="/authors/guide">Guide for Authors</a><a href="/for-authors">For authors</a>'
    assert guidelines.guideline_links(html, "https://example.org/j") == \
        ["https://example.org/authors/guide", "https://example.org/for-authors"]
