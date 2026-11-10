from app.storage import Database

REPORT = {"query": "q", "glance": [], "identity": {"title": "T", "issns": ["1234-5678"]}, "sources": {}}


def test_history_and_dirty_flags(tmp_path):
    db = Database(tmp_path / "a.db")
    uid = db.add_report(REPORT)
    assert db.list_reports()[0]["title"] == "T"
    assert db.get_report(uid)["uid"] == uid
    rows = db.dirty_rows()
    db.mark_clean([(r["uid"], r["updated_at"]) for r in rows])
    assert db.dirty_rows() == []
    db.delete_report(uid)
    assert db.get_report(uid) is None and db.dirty_rows()[0]["deleted"] is True


def test_merge_remote_newest_wins(tmp_path):
    db = Database(tmp_path / "b.db")
    uid = db.add_report(REPORT)
    old = {"uid": uid, "title": "old", "report": {}, "updated_at": 1.0, "deleted": False}
    assert db.merge_remote(old) is False
    assert db.merge_remote({"uid": "new", "title": "N", "report": {"x": 1}, "updated_at": 5.0, "created_at": 4.0}) is True
    assert db.get_report("new")["x"] == 1


def test_saved_journals(tmp_path):
    db = Database(tmp_path / "s.db")
    quick = {"query": "1532-0480", "quick": True, "identity": {"title": "JBI", "issns": ["1532-0480", "1532-0464"]}, "sources": {}}
    uid = db.save_journal(quick)
    assert uid == "1532-0464" and db.find_saved(["1532-0480"]) == uid
    assert db.list_saved()[0]["report"]["quick"] is True
    # a newer full report for the same journal replaces the saved copy automatically
    full = {"query": "1532-0464", "identity": {"title": "JBI", "issns": ["1532-0464"]}, "sources": {"x": 1}}
    db.add_report(full)
    assert db.get_saved(uid)["report"]["sources"] == {"x": 1}
    # reports for other journals leave it alone
    db.add_report({"query": "o", "identity": {"title": "Other", "issns": ["0000-0019"]}, "sources": {}})
    assert len(db.list_saved()) == 1
    # history can be cleared without touching saved journals
    for r in db.list_reports():
        db.delete_report(r["uid"])
    assert db.list_reports() == [] and len(db.list_saved()) == 1
    # title-only journals are keyed by title
    t = db.save_journal({"query": "x", "identity": {"title": "A Journal", "issns": []}, "sources": {}})
    assert t == "t:a journal"
    db.unsave(uid)
    assert [s["uid"] for s in db.list_saved()] == ["t:a journal"]
    assert {r["uid"] for r in db.dirty_saved()} == {uid, "t:a journal"}
    assert db.merge_remote_saved({"uid": "9999-9999", "title": "Remote", "issns": ["9999-9999"], "report": {"q": 1},
                                  "updated_at": 5.0, "created_at": 5.0}) is True



def test_history_shows_the_journal_name_not_the_issn(tmp_path):
    from app.storage import Database
    db = Database(tmp_path / "t.db")
    rep = {"query": "1532-0464", "identity": {"title": "1532-0464", "issns": ["1532-0464"]},
           "sources": {"scopus": {"status": "not_found", "data": {}},
                       "issn": {"status": "found", "data": {"title": "Journal of biomedical informatics"}},
                       "crest": {"status": "found", "data": {"title": "Journal of Biomedical Informatics"}}}}
    db.add_report(rep)
    assert db.list_reports()[0]["title"] == "Journal of Biomedical Informatics"
    with db._conn() as c:   # a row stored earlier under its ISSN is shown by name too
        c.execute("update reports set title = '1532-0464'")
    assert db.list_reports()[0]["title"] == "Journal of Biomedical Informatics"
    db.save_journal(rep)
    assert db.list_saved()[0]["title"] == "Journal of Biomedical Informatics"
