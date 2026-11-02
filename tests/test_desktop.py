"""Smoke test of the real desktop app (offscreen Qt + Qt WebEngine)."""

import os
import sys

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")


def test_window_loads_ui(tmp_path):
    from PySide6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    from app.storage import Database
    from desktop.app import Desktop

    Database(tmp_path / "journal_scope.db").add_report(
        {"query": "q", "glance": [], "identity": {"title": "Saved Journal", "issns": ["1234-5678"]}, "sources": {}})
    desk = Desktop(app, tmp_path, start_hidden=False)
    assert desk.win.isVisible()                    # shown before the server has loaded
    result = {}

    def check():
        desk.win.page.runJavaScript(
            "document.querySelector('#history b') && document.querySelector('#history b').textContent",
            0, lambda v: result.update(first=v) if v else None)

    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: (check(), result.get("first") and loop.quit()))
    timer.start(300)
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    timer.stop()
    links = []
    desk.win.page.app_link.disconnect(desk._app_link)   # don't open the real (modal) dialog
    desk.win.page.app_link.connect(links.append)
    from PySide6.QtCore import QUrl
    desk.win.page.acceptNavigationRequest(QUrl(desk.server.url + "desktop/phone-sync"), None, True)
    seen = {}
    desk.win.page.runJavaScript("!document.querySelector('#desk').hidden", 0, lambda v: seen.setdefault("settings", v))
    for _ in range(50):
        QCoreApplication.processEvents()
        if "settings" in seen:
            break
        import time; time.sleep(0.05)
    try:
        assert result.get("first") == "Saved Journal"
        assert links == ["phone-sync"] and seen.get("settings") is True
        assert desk.sync.status == "Phone sync off"
        assert "window shown after" in (tmp_path / "launch.log").read_text()
        assert "ready after" in (tmp_path / "launch.log").read_text()
    finally:
        desk.sync.stop()
        desk.renderer.shutdown()
        desk.win.shutdown()
        desk.server.stop()
        QCoreApplication.processEvents()
