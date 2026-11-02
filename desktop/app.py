"""Application entry point: single instance, tray icon, server, sync."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMenu,
                               QMessageBox, QSystemTrayIcon, QVBoxLayout)

from app.paths import default_data_dir
from app.storage import Database

from . import __version__, settings as settings_mod, updater, winplatform
from .sync import SyncEngine, protect_token, unprotect_token
from .sync_qt import SyncController, SyncDialog
from .renderer import QtRenderer
from .window import MainWindow, Server

SERVER_NAME = f"journal-scope-{getpass.getuser()}"
ICON = Path(__file__).resolve().parent.parent / "app" / "static" / "icon-512.png"


def _http_bytes(url: str) -> bytes:
    import urllib.request
    from app.fetch import USER_AGENT
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _signal_running_instance(show: bool) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(300):
        return False
    if show:
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def _append(path: Path, text: str, limit: int = 200_000) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > limit:
            path.write_text("", encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def _install_error_log(path: Path) -> None:
    """The app runs without a console (pythonw), so record unexpected errors in a file."""
    def hook(exc_type, exc, tb):
        _append(path, f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} (version {__version__})\n"
                      + "".join(traceback.format_exception(exc_type, exc, tb)) + "\n")
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook


T0 = float(os.environ.get("JOURNAL_SCOPE_T0") or time.time())   # set by run_journal_scope.py


def _since_start() -> str:
    return f"{time.time() - T0:.1f}s"


class ScopusKeyDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scopus API key")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        intro = QLabel('Scopus and CiteScore come from Elsevier\'s API. Get a free key at '
                       '<a href="https://dev.elsevier.com/apikey/manage">dev.elsevier.com</a> (sign in, "Create API Key"). '
                       "CiteScore quartiles need your institution's access: on the university network or VPN this works "
                       "with the key alone; otherwise ask the library for an institution token.")
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.key = QLineEdit(unprotect_token(settings.get("scopus_key", "")))
        self.token = QLineEdit(unprotect_token(settings.get("scopus_token", "")))
        self.token.setPlaceholderText("optional")
        form.addRow("API key", self.key)
        form.addRow("Institution token", self.token)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class Desktop:
    def __init__(self, app: QApplication, data_dir: Path, start_hidden: bool):
        self.app = app
        self.data_dir = data_dir
        self.settings_path = data_dir / "settings.json"
        self.settings = settings_mod.load(self.settings_path)
        self.restart_after_update = False
        self.icon = QIcon(str(ICON))
        self.db = Database(data_dir / "journal_scope.db")

        self.log = data_dir / "launch.log"
        self.renderer = QtRenderer(data_dir, app)
        self.server = Server(self.db, self._status, self._on_local_change, renderer=self.renderer.submit,
                             scopus_credentials=self._scopus_credentials)
        self.server.start()                     # loads in the background
        self.engine = SyncEngine(self.db, self.settings, self._save_settings, self.server.lookup,
                                 get_bytes=_http_bytes, tmp_dir=data_dir / "lists")
        self.sync = SyncController(self.engine)

        self.win = MainWindow(self.server.url, self.icon)
        self.sync.reports_changed.connect(self.win.refresh_history)
        self.menu = self._build_menu()
        self.win.page.app_link.connect(self._app_link)
        self.tray = self._build_tray()
        if not start_hidden:
            self.win.show_and_raise()
        _append(self.log, f"  window shown after {_since_start()}\n")

        self._wait = QTimer()
        self._wait.setInterval(50)
        self._wait.timeout.connect(self._when_server_ready)
        self._wait.start()

    def _when_server_ready(self) -> None:
        if not self.server.ready.is_set():
            return
        self._wait.stop()
        if self.server.error:
            self.win.show_error(self.server.error)
            _append(self.log, f"  server FAILED after {_since_start()}: {self.server.error}\n")
            return
        self.win.load_app()
        self.sync.start()
        _append(self.log, f"  ready after {_since_start()}\n")

    # ---------------------------------------------------------------- hooks
    def _status(self) -> dict:
        return {"sync": self.sync.status if hasattr(self, "sync") else "", "version": __version__}

    def _on_local_change(self, _uid: str) -> None:
        if hasattr(self, "sync"):
            self.sync.kick()

    def _save_settings(self, s: dict) -> None:
        settings_mod.save(self.settings_path, s)

    def _scopus_credentials(self) -> tuple[str | None, str | None]:
        return (unprotect_token(self.settings.get("scopus_key", "")) or None,
                unprotect_token(self.settings.get("scopus_token", "")) or None)

    def open_scopus_key(self) -> None:
        self.win.show_and_raise()
        dlg = ScopusKeyDialog(self.settings, self.win)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            key, token = dlg.key.text().strip(), dlg.token.text().strip()
            self.settings["scopus_key"] = protect_token(key) if key else ""
            self.settings["scopus_token"] = protect_token(token) if token else ""
            self._save_settings(self.settings)

    def refresh_lists_now(self) -> None:
        if not self.engine.configured:
            QMessageBox.information(self.win, "Journal Scope", "Turn on Phone sync first (Settings → Phone sync…).")
            return
        self.engine.force_lists = True
        self.sync.kick()
        QMessageBox.information(self.win, "Journal Scope",
                                "Reloading the CREST and SANLiC lists for the phone. This takes a minute or two; "
                                "the status at the bottom left shows when it is done.")

    # ------------------------------------------------------- menu and tray
    def _app_link(self, action: str) -> None:
        """The Settings button in the window."""
        if action == "phone-sync":
            self.open_sync()
        elif action == "menu":
            self.menu.popup(QCursor.pos())

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.addAction("Open Journal Scope", self.win.show_and_raise)
        menu.addAction("Scopus API key…", self.open_scopus_key)
        menu.addAction("Phone sync…", self.open_sync)
        menu.addAction("Refresh phone lists now", self.refresh_lists_now)
        if winplatform.IS_WINDOWS:
            startup = QAction("Start with Windows", menu, checkable=True)
            startup.setChecked(winplatform.start_with_windows_enabled())   # a file check, no PowerShell
            startup.toggled.connect(self._toggle_startup)
            menu.addAction(startup)
            menu.addAction("Add desktop shortcut", self._desktop_shortcut)
        if updater.repo_root() is not None:
            menu.addAction("Update and restart", self.update_and_restart)
        menu.addSeparator()
        menu.addAction("Quit", self.app.quit)
        return menu

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.icon, self.app)
        tray.setToolTip("Journal Scope")
        menu = self.menu
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self.win.show_and_raise() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
        tray._menu = menu   # keep a reference
        return tray

    def open_sync(self) -> None:
        self.win.show_and_raise()
        SyncDialog(self.sync, self.win).exec()

    def _toggle_startup(self, on: bool) -> None:
        try:
            winplatform.set_start_with_windows(on)
        except OSError as exc:
            QMessageBox.warning(self.win, "Journal Scope", f"Could not change the startup setting: {exc}")

    def _desktop_shortcut(self) -> None:
        lnk = winplatform.desktop_shortcut()
        try:
            if lnk is None:
                raise OSError("Desktop folder not found")
            winplatform.create_shortcut(lnk)
            QMessageBox.information(self.win, "Journal Scope", f"Shortcut created:\n{lnk}")
        except OSError as exc:
            QMessageBox.warning(self.win, "Journal Scope", f"Could not create the shortcut: {exc}")

    def update_and_restart(self) -> None:
        self.restart_after_update = True
        self.app.quit()

    def shutdown(self) -> None:
        self._wait.stop()
        self.sync.stop()
        self.renderer.shutdown()
        self.win.shutdown()
        self.server.stop()
        if self.restart_after_update or self.settings.get("auto_update", True):
            updater.spawn_update(self.data_dir / "update.log", restart=self.restart_after_update)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="journal-scope", description="Journal credibility checker")
    parser.add_argument("--data-dir", type=Path, help="folder for the database, settings and logs")
    parser.add_argument("--startup", action="store_true", help="started by Windows at sign-in (start in the tray)")
    parser.add_argument("--version", action="version", version=__version__)
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)   # required by Qt WebEngine
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Journal Scope")
    app.setOrganizationName("JournalScope")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(True)    # closing the window quits
    app.setWindowIcon(QIcon(str(ICON)))

    if _signal_running_instance(show=not args.startup):
        return 0
    local = QLocalServer(app)
    QLocalServer.removeServer(SERVER_NAME)
    local.listen(SERVER_NAME)

    data_dir = args.data_dir or default_data_dir()
    _install_error_log(data_dir / "error.log")
    _append(data_dir / "launch.log", f"{time.strftime('%Y-%m-%d %H:%M:%S')} v{__version__} "
                                     f"{'AT-STARTUP' if args.startup else 'manual'} exe={sys.executable} "
                                     f"(Qt up after {_since_start()})\n", 100_000)

    desk = Desktop(app, data_dir, start_hidden=args.startup)

    def on_connection() -> None:
        conn = local.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: (conn.readAll(), desk.win.show_and_raise()))

    local.newConnection.connect(on_connection)
    app.aboutToQuit.connect(desk.shutdown)
    return app.exec()
