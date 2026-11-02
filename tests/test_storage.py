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
