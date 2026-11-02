"""Page rendering inside the app's own Qt WebEngine (Chromium).

Pages load in the app's embedded browser, which identifies itself as usual.
Cookies are kept in a profile under the data folder, the same way an
ordinary browser keeps them between visits.

When a site shows an "are you a person" check (SCImago and some publishers
use Cloudflare), the page is opened in a window for you to complete it
yourself; the lookup then carries on. Nothing tries to answer the check
automatically.

The lookup engine runs on the web server's thread; `submit()` is thread-safe
and returns a concurrent.futures.Future with the rendered HTML.
"""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import Future
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

CHALLENGE_JS = r"""(() => {
  const head = document.documentElement ? document.documentElement.outerHTML.slice(0, 30000) : "";
  const blocked = /cf-chl-|challenge-platform\/h\/|Just a moment\.\.\.|Attention Required! \| Cloudflare|Checking your browser before accessing|Please enable JavaScript and cookies to continue/.test(head);
  return JSON.stringify({ blocked, ready: %s, loaded: document.readyState === "complete" });
})()"""

SHOW_CHECK_AFTER_S = 2   # a check page is shown to the user as soon as it has rendered
HUMAN_CHECK_S = 60       # time allowed to complete it (the source's own limit also applies)


class FetchFailed(Exception):
    pass


class _Job:
    def __init__(self, url: str, wait_for: str | None, wait_ms: int, future: Future, dest: Path | None = None):
        self.url, self.wait_for, self.wait_ms, self.future = url, wait_for, wait_ms, future
        self.dest = dest                     # set for file downloads
        self.started = time.time()
        self.ready_since: float | None = None
        self.page: QWebEnginePage | None = None
        self.timer: QTimer | None = None
        self.dialog: QDialog | None = None
        self.view: QWebEngineView | None = None
        self.done = False


class QtRenderer(QObject):
    _submit = Signal(object)

    def __init__(self, data_dir: Path, parent=None, allow_human_check: bool = True):
        super().__init__(parent)
        self.allow_human_check = allow_human_check
        storage = Path(data_dir) / "browser"
        name = "journal-scope-fetch-" + hashlib.sha1(str(storage.resolve()).encode()).hexdigest()[:8]
        self.profile = QWebEngineProfile(name, self)
        storage.mkdir(parents=True, exist_ok=True)
        self.profile.setPersistentStoragePath(str(storage))
        self.profile.setCachePath(str(storage / "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.setHttpAcceptLanguage("en-GB,en;q=0.9")
        self._jobs: set[_Job] = set()
        self._submit.connect(self._start)
        self.profile.downloadRequested.connect(self._on_download)

    # -------------------------------------------------------------- public
    def submit(self, url: str, wait_for: str | None = None, wait_ms: int = 15000) -> Future:
        fut: Future = Future()
        self._submit.emit(_Job(url, wait_for, wait_ms, fut))
        return fut

    def download(self, url: str, dest: Path) -> Future:
        """Download a file through the embedded browser (resolves to `dest`)."""
        fut: Future = Future()
        self._submit.emit(_Job(url, None, 0, fut, dest=Path(dest)))
        return fut

    def shutdown(self) -> None:
        for job in list(self._jobs):
            self._finish(job, error="Journal Scope is closing")

    # ------------------------------------------------------------- per job
    def _start(self, job: _Job) -> None:
        self._jobs.add(job)
        job.page = QWebEnginePage(self.profile, self)
        job.page.load(QUrl(job.url))
        if job.dest is not None:
            ready = "false"          # a download never becomes a page
        elif job.wait_for:
            ready = f"!!document.querySelector({json.dumps(job.wait_for)})"
        else:
            ready = "document.readyState === 'complete'"
        script = CHALLENGE_JS % ready
        job.timer = QTimer(self)
        job.timer.setInterval(400)
        job.timer.timeout.connect(lambda: job.page and job.page.runJavaScript(script, 0, lambda r: self._check(job, r)))
        job.timer.start()

    def _check(self, job: _Job, raw) -> None:
        if job.done:
            return
        if job.future.cancelled():          # the lookup gave up on this source
            self._finish(job, error="cancelled")
            return
        try:
            st = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            st = {}
        elapsed = time.time() - job.started
        blocked = bool(st.get("blocked"))

        if not blocked and st.get("ready"):
            job.ready_since = job.ready_since or time.time()
            if time.time() - job.ready_since >= 0.3:   # let late scripts settle
                job.page.toHtml(lambda html: self._finish(job, html=html))
            return
        if blocked:
            if elapsed > SHOW_CHECK_AFTER_S and job.dialog is None:
                if not self.allow_human_check:
                    self._finish(job, error=f"{job.url} asked for a human check; open Journal Scope on the PC to complete it")
                    return
                self._show_check(job)
            if elapsed > SHOW_CHECK_AFTER_S + HUMAN_CHECK_S:
                self._finish(job, error=f"{job.url}: the site's 'are you human' check was not completed")
            return
        if job.dest is not None:
            if elapsed > 180:
                self._finish(job, error=f"Download of {job.url} did not start")
            return
        if elapsed * 1000 > job.wait_ms and st.get("loaded"):
            job.page.toHtml(lambda html: self._finish(job, html=html))   # return what loaded
        elif elapsed > 60:
            self._finish(job, error=f"Timed out loading {job.url}")

    def _on_download(self, req) -> None:
        job = next((j for j in self._jobs if j.dest is not None and j.page is req.page() and not j.done), None)
        if job is None:
            req.cancel()
            return
        job.dest.parent.mkdir(parents=True, exist_ok=True)
        req.setDownloadDirectory(str(job.dest.parent))
        req.setDownloadFileName(job.dest.name)
        req.isFinishedChanged.connect(lambda: self._download_done(job, req))
        req.accept()

    def _download_done(self, job: _Job, req) -> None:
        if not req.isFinished():
            return
        if req.state() == req.DownloadState.DownloadCompleted:
            self._finish(job, html=str(job.dest))
        else:
            self._finish(job, error=f"Download failed: {req.interruptReasonString()}")

    def _show_check(self, job: _Job) -> None:
        host = QUrl(job.url).host()
        dlg = QDialog()
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)   # never hidden behind the main window
        dlg.setWindowTitle(f"Confirm on {host}")
        dlg.resize(560, 640)
        lay = QVBoxLayout(dlg)
        note = QLabel(f"{host} wants to confirm a person is using the browser. Complete the check below; "
                      "the lookup continues by itself and this window closes. It is usually needed only once.")
        note.setWordWrap(True)
        lay.addWidget(note)
        job.view = QWebEngineView(dlg)
        job.view.setPage(job.page)
        lay.addWidget(job.view, 1)
        dlg.rejected.connect(lambda: self._finish(job, error=f"{host} check was cancelled"))
        job.dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _finish(self, job: _Job, html: str | None = None, error: str | None = None) -> None:
        if job.done:
            return
        job.done = True
        if job.timer:
            job.timer.stop()
            job.timer.deleteLater()
        if not job.future.done():
            if error:
                job.future.set_exception(FetchFailed(error))
            else:
                job.future.set_result(html or "")
        # Qt WebEngine: detach the view before deleting the page.
        if job.view is not None:
            job.view.setPage(None)
        if job.dialog is not None:
            try:
                job.dialog.rejected.disconnect()
            except (RuntimeError, TypeError):
                pass
            job.dialog.close()
            job.dialog.deleteLater()
        if job.page is not None:
            job.page.deleteLater()
        self._jobs.discard(job)
