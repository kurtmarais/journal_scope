import time

import pytest

from app.storage import Database
from desktop import reflists
from desktop.sync import SyncEngine
from tests.fake_supabase import EMAIL, PASSWORD, FakeSupabase
from tests.reflist_samples import crest_xlsx, get_bytes_for, sanlic_xlsx


def test_parse_crest_and_sanlic(tmp_path):
    crest_xlsx(tmp_path / "c.xlsx")
    rows = reflists.parse_crest_xlsx(tmp_path / "c.xlsx")
    assert rows[0] == {"key": "JNL_014249", "title": "Journal of Biomedical Informatics", "title_norm": "journal of biomedical informatics",
                       "variants": None, "publisher": "Elsevier", "issn": "1532-0464", "eissn": "1532-0480",
                       "status": "Eligible for subsidy", "reason": None}
    assert rows[1]["status"] == "Discontinued" and rows[1]["eissn"] is None
    sanlic_xlsx(tmp_path / "s.xlsx")
    s = reflists.sanlic_rows(tmp_path / "s.xlsx")
    assert s[0]["included"] is True and s[0]["issns"] == ["1532-0464"] and s[1]["included"] is False


def test_crest_link():
    html = '<a href="https://db.crest.sun.ac.za/zapublications/downloadfile/downloadable_journal_master_list_20260811.xlsx">'
    assert reflists.crest_file_url(html).endswith("downloadable_journal_master_list_20260811.xlsx")


def test_due():
    assert reflists.due({}) is True
    now = time.time()
    ok = {"crest": "ok, 10 journals", "sanlic": "ok, 5 journals", "sjr": "ok, 30000 journals"}
    assert reflists.due({"ref_refreshed_at": now - 3600, "ref_results": ok}) is False
    assert reflists.due({"ref_refreshed_at": now - 8 * 86400, "ref_tried_at": now - 3600}) is False
    # a list added since the last weekly refresh (SCImago) loads the next day, not a week later
    old = {"crest": "ok, 10 journals", "sanlic": "ok, 5 journals"}
    assert reflists.due({"ref_refreshed_at": now - 2 * 86400, "ref_tried_at": now - 2 * 86400, "ref_results": old}) is True
    assert reflists.due({"ref_refreshed_at": now - 2 * 86400, "ref_tried_at": now - 2 * 86400, "ref_results": ok}) is False


def _engine(tmp_path, fake, get_bytes=None):
    eng = SyncEngine(Database(tmp_path / "d.db"), {}, lambda s: None, lambda *a: {}, opener=fake.opener,
                     get_bytes=get_bytes or get_bytes_for(tmp_path), tmp_dir=tmp_path / "lists")
    eng.sign_in("https://x.supabase.co", "k", EMAIL, PASSWORD)
    return eng


def test_cycle_loads_lists_and_replaces_old_rows(tmp_path):
    fake = FakeSupabase()
    fake.tables["ref_crest"]["stale"] = {"key": "stale", "batch": "old"}
    eng = _engine(tmp_path, fake)
    result = eng.cycle()
    assert all(v.startswith("ok") for v in result["lists"].values()), result
    assert set(fake.tables["ref_crest"]) == {"JNL_014249", "JNL_024590"}      # stale row removed
    assert fake.tables["ref_crest"]["JNL_014249"]["status"] == "Eligible for subsidy"
    assert len(fake.tables["ref_sanlic"]) == 2
    assert fake.tables["ref_lists"]["crest"]["rows"] == 2 and fake.tables["ref_lists"]["sanlic"]["rows"] == 2
    assert eng.settings["ref_refreshed_at"] > 0
    assert eng.cycle()["lists"] is None          # not due again for a week


def test_one_list_failing_keeps_the_others(tmp_path):
    fake = FakeSupabase()

    good = get_bytes_for(tmp_path)

    def flaky(url):
        if "crest" in url:
            raise OSError("CREST unreachable")
        return good(url)

    eng = _engine(tmp_path, fake, get_bytes=flaky)
    lists = eng.cycle()["lists"]
    assert lists["crest"].startswith("failed") and lists["sanlic"].startswith("ok")
    assert "ref_refreshed_at" not in eng.settings      # retried tomorrow
    assert fake.tables["ref_sanlic"]


def test_scimago_list_is_loaded_for_the_phone(tmp_path):
    from tests.reflist_samples import sjr_rows
    fake = FakeSupabase()
    eng = SyncEngine(Database(tmp_path / "d.db"), {}, lambda s: None, lambda *a: {}, opener=fake.opener,
                     get_bytes=get_bytes_for(tmp_path), tmp_dir=tmp_path / "lists", sjr_rows=sjr_rows)
    eng.sign_in("https://x.supabase.co", "k", EMAIL, PASSWORD)
    lists = eng.cycle()["lists"]
    assert lists["sjr"] == "ok, 4 journals"
    row = fake.tables["ref_sjr"]["23571"]
    assert row["quartile"] == "Q1" and row["sjr"] == 1.612 and row["issns"] == ["1532-0480", "1532-0464"]
    assert row["categories"][1] == {"name": "Health Informatics", "quartile": "Q1"} and row["year"] == "2025"
    assert fake.tables["ref_lists"]["sjr"]["version"] == "SJR 2025"


def test_scimago_failing_keeps_the_other_lists(tmp_path):
    fake = FakeSupabase()

    def blocked():
        raise RuntimeError("scimagojr.com asked for a human check")

    eng = SyncEngine(Database(tmp_path / "d.db"), {}, lambda s: None, lambda *a: {}, opener=fake.opener,
                     get_bytes=get_bytes_for(tmp_path), tmp_dir=tmp_path / "lists", sjr_rows=blocked)
    eng.sign_in("https://x.supabase.co", "k", EMAIL, PASSWORD)
    lists = eng.cycle()["lists"]
    assert lists["sjr"].startswith("failed: scimagojr.com asked") and lists["crest"].startswith("ok")
    assert fake.tables["ref_crest"] and not fake.tables["ref_sjr"]
