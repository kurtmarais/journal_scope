"""Qt WebEngine renderer used by the desktop app (offscreen)."""

import functools
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

PAGES = {
    "/plain": "<html><body><div class='journalgrid'>SJR 1.3</div></body></html>",
    "/late": "<html><body><script>setTimeout(() => { const d = document.createElement('div');"
             " d.className = 'journalgrid'; d.textContent = 'filled by script'; document.body.append(d); }, 700)</script></body></html>",
    "/check": "<html><head><title>Just a moment...</title></head><body>checking</body></html>",
}


@pytest.fixture(scope="module")
def site():
    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = PAGES.get(self.path, "missing").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    return QApplication.instance() or QApplication([sys.argv[0]])


def wait(qapp, fut, limit=30):
    end = time.time() + limit
    while not fut.done() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    return fut


@pytest.fixture(scope="module")
def renderer(qapp, tmp_path_factory):
    from desktop.renderer import QtRenderer
    path = tmp_path_factory.mktemp("data")
    r = QtRenderer(path, allow_human_check=False)
    r.data_dir = path
    yield r
    r.shutdown()
    for _ in range(20):
        qapp.processEvents()


def test_renders_pages(qapp, site, renderer):
    r = renderer
    assert "SJR 1.3" in wait(qapp, r.submit(site + "/plain", "div.journalgrid")).result()
    assert "filled by script" in wait(qapp, r.submit(site + "/late", "div.journalgrid")).result()
    assert (r.data_dir / "browser").is_dir()


def test_check_page_is_reported_not_bypassed(qapp, site, renderer):
    from desktop.renderer import FetchFailed
    fut = wait(qapp, renderer.submit(site + "/check", "div.journalgrid"))
    with pytest.raises(FetchFailed, match="human check"):
        fut.result()
