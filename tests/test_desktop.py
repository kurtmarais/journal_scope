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
    result = {}

    def check():
        desk.win.page.runJavaScript(
            "document.querySelector('#history b') && document.querySelector('#history b').textContent",
            0, lambda v: result.setdefault("first", v))

    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: (check(), result.get("first") and loop.quit()))
    timer.start(300)
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    timer.stop()
    try:
        assert result.get("first") == "Saved Journal"
        assert desk.sync.status == "Phone sync off"
    finally:
        desk.sync.stop()
        desk.win.shutdown()
        desk.server.stop()
        QCoreApplication.processEvents()
