"""Desktop launcher: runs the server in the background and opens a window.

    python -m app.desktop

Uses a native window (pywebview) when available, otherwise opens the default
browser. Closing the window stops the server. Set JOURNAL_SCOPE_BROWSER=1 to
always use the browser.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .main import app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(url: str, timeout: float = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main() -> None:
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not _wait_until_up(url):
        raise SystemExit("Journal Scope server did not start.")

    use_window = os.environ.get("JOURNAL_SCOPE_BROWSER") != "1"
    if use_window:
        try:
            import webview  # pywebview

            webview.create_window("Journal Scope", url, width=1200, height=900, min_size=(420, 600))
            webview.start()  # blocks until the window is closed
            server.should_exit = True
            thread.join(timeout=10)
            return
        except Exception as exc:  # no native webview on this system
            print(f"Native window unavailable ({exc}); opening your browser instead.", flush=True)

    webbrowser.open(url)
    print(f"Journal Scope is running at {url} - close this window or press Ctrl+C to stop.", flush=True)
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
