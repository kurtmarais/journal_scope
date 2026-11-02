import time

import pytest

from app.storage import Database
from desktop.sync import AuthError, SupabaseClient, SyncEngine, protect_token, unprotect_token
from tests.fake_supabase import EMAIL, PASSWORD, FakeSupabase

REPORT = {"query": "1532-0464", "candidates": [], "glance": [{"label": "DHET subsidy", "value": "Eligible", "tone": "good", "source": "crest"}],
          "identity": {"title": "Journal of Biomedical Informatics", "issns": ["1532-0464"]}, "sources": {}}


@pytest.fixture
def setup(tmp_path):
    fake = FakeSupabase()
    db = Database(tmp_path / "js.db")
    saved = {}
    calls = []

    def lookup(q, sid, site):
        calls.append((q, sid, site))
        if q == "boom":
            raise RuntimeError("Scopus unreachable")
        if q == "Nature":
            return {"query": q, "candidates": [{"source_id": "1", "title": "Nature"}, {"source_id": "2", "title": "Nature Medicine"}]}
        return dict(REPORT, query=q)

    engine = SyncEngine(db, {}, lambda s: saved.update(s), lookup, opener=fake.opener)
    engine.sign_in("https://x.supabase.co", "sb_publishable_x", EMAIL, PASSWORD)
    return fake, db, engine, calls, saved


def test_token_storage_roundtrip():
    assert unprotect_token(protect_token("abc")) == "abc"
    assert unprotect_token("") == ""


def test_bad_password(tmp_path):
    fake = FakeSupabase()
    eng = SyncEngine(Database(tmp_path / "d.db"), {}, lambda s: None, lambda *a: {}, opener=fake.opener)
    with pytest.raises(AuthError):
        eng.sign_in("https://x.supabase.co", "k", EMAIL, "wrong")


def test_sign_in_does_not_store_password(setup):
    _, _, engine, _, saved = setup
    assert PASSWORD not in str(saved) and saved["sync_email"] == EMAIL and saved["sync_token"]


def test_local_report_uploads_and_phone_delete_comes_back(setup):
    fake, db, engine, _, _ = setup
    uid = db.add_report(REPORT)
    assert engine.cycle()["uploaded"] == 1
    remote = fake.tables["journal_reports"][uid]
    assert remote["title"] == "Journal of Biomedical Informatics" and remote["report"]["query"] == "1532-0464"
    assert db.dirty_rows() == []
    # deleted on the phone
    fake.tables["journal_reports"][uid].update(deleted=True, report={}, updated_at=time.time() + 5,
                                               server_updated_at=fake._stamp())
    assert engine.cycle()["downloaded"] == 1
    assert db.get_report(uid) is None and db.list_reports() == []


def test_phone_requests_are_looked_up(setup):
    fake, db, engine, calls, _ = setup
    t = time.time()
    for uid, q in (("r1", "1532-0464"), ("r2", "Nature"), ("r3", "boom")):
        fake.tables["journal_requests"][uid] = {"uid": uid, "query": q, "sid": None, "site": None, "status": "pending",
                                                "deleted": False, "created_at": t, "updated_at": t,
                                                "server_updated_at": fake._stamp(), "report_uid": None,
                                                "candidates": None, "error": None}
    result = engine.cycle()
    assert result["looked_up"] == 2
    reqs = fake.tables["journal_requests"]
    assert reqs["r1"]["status"] == "done" and reqs["r1"]["report_uid"] in fake.tables["journal_reports"]
    assert db.get_report(reqs["r1"]["report_uid"])["identity"]["title"] == "Journal of Biomedical Informatics"
    assert reqs["r2"]["status"] == "done" and len(reqs["r2"]["candidates"]) == 2 and reqs["r2"]["report_uid"] is None
    assert reqs["r3"]["status"] == "error" and "Scopus unreachable" in reqs["r3"]["error"]
    assert [c[0] for c in calls] == ["1532-0464", "Nature", "boom"]
    assert engine.cycle()["looked_up"] == 0   # nothing pending any more


def test_expired_session_refreshes(setup):
    fake, db, engine, _, _ = setup
    fake.access.clear()            # server forgets access tokens
    engine._session["expires_at"] = 0
    db.add_report(REPORT)
    assert engine.cycle()["uploaded"] == 1


def test_client_paginates(setup):
    fake, _, engine, _, _ = setup
    for i in range(3):
        fake.tables["journal_reports"][f"x{i}"] = {"uid": f"x{i}", "updated_at": 1, "server_updated_at": fake._stamp(), "deleted": False}
    client = SupabaseClient("https://x.supabase.co", "k", fake.opener)
    import desktop.sync as s
    old, s.PAGE = s.PAGE, 2
    try:
        assert len(client.changes_since("journal_reports", "", engine._token())) == 3
    finally:
        s.PAGE = old
