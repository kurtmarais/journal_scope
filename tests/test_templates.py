"""Template/guideline links (web/report-view.js), run with Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
NODE = shutil.which("node")


def run_js(tmp_path, code: str):
    mod = tmp_path / "report-view.mjs"
    mod.write_text((WEB / "report-view.js").read_text())
    script = f"import * as rv from {json.dumps(str(mod))};\n{code}"
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_guideline_urls(tmp_path):
    cases = {
        "https://www.journals.elsevier.com/journal-of-biomedical-informatics": "https://www.sciencedirect.com/journal/journal-of-biomedical-informatics/publish/guide-for-authors",
        "https://www.sciencedirect.com/journal/journal-of-biomedical-informatics": "https://www.sciencedirect.com/journal/journal-of-biomedical-informatics/publish/guide-for-authors",
        "https://link.springer.com/journal/10916": "https://link.springer.com/journal/10916/submission-guidelines",
        "https://www.mdpi.com/journal/sensors": "https://www.mdpi.com/journal/sensors/instructions",
        "https://www.tandfonline.com/toc/tjis20/current": "https://www.tandfonline.com/action/authorSubmission?show=instructions&journalCode=tjis20",
        "https://journals.sagepub.com/home/dhj": "https://journals.sagepub.com/author-instructions/DHJ",
        "https://onlinelibrary.wiley.com/journal/15206688": "https://onlinelibrary.wiley.com/page/journal/15206688/homepage/forauthors.html",
        "https://example.org/journal": None,
        "not a url": None,
    }
    got = run_js(tmp_path, f"console.log(JSON.stringify({json.dumps(list(cases))}.map(rv.journalGuidelinesUrl)))")
    assert got == list(cases.values())


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_template_links_for_report(tmp_path):
    report = {"query": "1532-0464", "identity": {"title": "Journal of Biomedical Informatics", "publisher": "Academic Press Inc.",
                                                 "homepage": "https://www.journals.elsevier.com/journal-of-biomedical-informatics"},
              "sources": {}}
    links = run_js(tmp_path, f"console.log(JSON.stringify(rv.templateLinks({json.dumps(report)})))")
    labels = [l["label"] for l in links]
    assert labels == ["Author guidelines for this journal", "Elsevier templates and formatting", "Elsevier templates on Overleaf",
                      "Search Overleaf for this journal's template", "Citation style (Zotero / Mendeley / EndNote)"]
    assert links[3]["url"].startswith("https://www.google.com/search?q=site%3Aoverleaf.com%2Flatex%2Ftemplates")
    assert links[4]["url"] == "https://www.zotero.org/styles?q=Journal%20of%20Biomedical%20Informatics"

    # Unknown publisher, no homepage: still the Overleaf search and citation style
    bare = {"query": "Some Journal", "identity": {"title": "Some Journal"}, "sources": {}}
    labels = [l["label"] for l in run_js(tmp_path, f"console.log(JSON.stringify(rv.templateLinks({json.dumps(bare)})))")]
    assert labels == ["Search Overleaf for this journal's template", "Citation style (Zotero / Mendeley / EndNote)"]
