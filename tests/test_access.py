import importlib

from fastapi.testclient import TestClient


def make_client(monkeypatch, key):
    if key:
        monkeypatch.setenv("JOURNAL_SCOPE_KEY", key)
    else:
        monkeypatch.delenv("JOURNAL_SCOPE_KEY", raising=False)
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_open_without_key(monkeypatch):
    c = make_client(monkeypatch, None)
    assert c.get("/").status_code == 200
    assert c.get("/manifest.webmanifest").json()["name"] == "Journal Scope"


def test_key_required(monkeypatch):
    c = make_client(monkeypatch, "s3cret")
    assert c.get("/").status_code == 401
    assert c.get("/static/styles.css").status_code == 200
    r = c.get("/?key=s3cret&q=1532-0464", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?q=1532-0464"
    assert c.get("/").status_code == 200  # cookie remembered
    assert make_client(monkeypatch, "s3cret").get("/?key=wrong", follow_redirects=False).status_code == 401
    make_client(monkeypatch, None)
