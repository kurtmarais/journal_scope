"""Main window: the local web UI in a Qt WebEngine view, plus its server thread."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import urllib.request

import uvicorn
from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

from app import main as web


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """Runs the FastAPI app on 127.0.0.1 in a background thread."""

    def __init__(self, db, status, on_report, renderer=None):
        web.app.state.db = db
        web.app.state.renderer = renderer
        web.app.state.status = status
        web.app.state.on_report = on_report
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        self._server = uvicorn.Server(uvicorn.Config(web.app, host="127.0.0.1", port=self.port,
                                                     log_level="warning", log_config=None))
        self._thread = threading.Thread(target=self._server.run, name="journal-scope-web", daemon=True)

    def start(self, timeout: float = 30) -> None:
        self._thread.start()
        end = time.time() + timeout
        while time.time() < end:
            try:
                urllib.request.urlopen(self.url + "api/status", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("The Journal Scope web server did not start")

    def lookup(self, q: str, sid: str | None, site: str | None) -> dict:
        """Run a lookup on the server's event loop (called from the sync thread)."""
        fut = asyncio.run_coroutine_threadsafe(web.run_lookup(q, sid, site), web.app.state.loop)
        return fut.result(timeout=600)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


class _Page(QWebEnginePage):
    """Keeps the app inside the window; every outside link opens in the default browser.
    Links to /desktop/<action> (the Settings button) are handed to the desktop app."""

    app_link = Signal(str)

    def __init__(self, base: str, parent=None):
        super().__init__(parent)
        self.base = base

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:
        if url.toString().startswith(self.base + "desktop/"):
            self.app_link.emit(url.path().rsplit("/", 1)[-1])
            return False
        if url.toString().startswith(self.base) or url.scheme() in ("data", "about"):
            return True
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        return False

    def createWindow(self, _type):
        # target="_blank": hand the URL to the browser via a throwaway page.
        page = _Page(self.base, self)
        page.urlChanged.connect(lambda u: (QDesktopServices.openUrl(u), page.deleteLater()))
        return page


class MainWindow(QMainWindow):
    def __init__(self, url: str, icon: QIcon):
        super().__init__()
        self.setWindowTitle("Journal Scope")
        self.setWindowIcon(icon)
        self.resize(1200, 860)
        self.view = QWebEngineView(self)
        self.page = _Page(url, self.view)
        self.view.setPage(self.page)
        self.view.setUrl(QUrl(url + "?desktop=1"))
        self.setCentralWidget(self.view)
        self.hide_on_close = True
        self.on_first_hide = None

    def refresh_history(self) -> None:
        self.page.runJavaScript("window.journalScope && window.journalScope.refreshHistory()")

    def show_and_raise(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~self.windowState().WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if self.hide_on_close:
            event.ignore()
            self.hide()
            if self.on_first_hide:
                self.on_first_hide()
                self.on_first_hide = None
        else:
            event.accept()

    def shutdown(self) -> None:
        # Qt WebEngine: delete the view before its page goes away.
        self.hide_on_close = False
        self.view.setPage(None)
        self.page.deleteLater()
        self.view.deleteLater()
