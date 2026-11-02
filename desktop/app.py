"""Application entry point: single instance, tray icon, server, sync."""

from __future__ import annotations

import argparse
import getpass
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from app.paths import default_data_dir
from app.storage import Database

from . import __version__, settings as settings_mod, updater, winplatform
from .sync import SyncEngine
from .sync_qt import SyncController, SyncDialog
from .window import MainWindow, Server

SERVER_NAME = f"journal-scope-{getpass.getuser()}"
ICON = Path(__file__).resolve().parent.parent / "app" / "static" / "icon-512.png"


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


class Desktop:
    def __init__(self, app: QApplication, data_dir: Path, start_hidden: bool):
        self.app = app
        self.data_dir = data_dir
        self.settings_path = data_dir / "settings.json"
        self.settings = settings_mod.load(self.settings_path)
        self.restart_after_update = False
        self.icon = QIcon(str(ICON))
        self.db = Database(data_dir / "journal_scope.db")

        self.server = Server(self.db, self._status, self._on_local_change)
        self.server.start()
        self.engine = SyncEngine(self.db, self.settings, self._save_settings, self.server.lookup)
        self.sync = SyncController(self.engine)

        self.win = MainWindow(self.server.url, self.icon)
        self.sync.reports_changed.connect(self.win.refresh_history)
        self.tray = self._build_tray()
        if self.tray is None:
            self.win.hide_on_close = False
            app.setQuitOnLastWindowClosed(True)
        else:
            self.win.on_first_hide = lambda: self.tray.showMessage(
                "Journal Scope", "Still running in the tray, so phone lookups keep working. Right-click the icon to quit.",
                self.icon, 4000)
        if not start_hidden:
            self.win.show_and_raise()
        self.sync.start()

    # ---------------------------------------------------------------- hooks
    def _status(self) -> dict:
        return {"sync": self.sync.status if hasattr(self, "sync") else "", "version": __version__}

    def _on_local_change(self, _uid: str) -> None:
        if hasattr(self, "sync"):
            self.sync.kick()

    def _save_settings(self, s: dict) -> None:
        settings_mod.save(self.settings_path, s)

    # ----------------------------------------------------------------- tray
    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.icon, self.app)
        tray.setToolTip("Journal Scope")
        menu = QMenu()
        menu.addAction("Open Journal Scope", self.win.show_and_raise)
        menu.addAction("Phone sync…", self.open_sync)
        if winplatform.IS_WINDOWS:
            startup = QAction("Start with Windows", menu, checkable=True)
            startup.setChecked(winplatform.start_with_windows_enabled())
            startup.toggled.connect(self._toggle_startup)
            menu.addAction(startup)
            menu.addAction("Add desktop shortcut", self._desktop_shortcut)
        if updater.repo_root() is not None:
            menu.addAction("Update and restart", self.update_and_restart)
        menu.addSeparator()
        menu.addAction("Quit", self.app.quit)
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
        self.sync.stop()
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
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(ICON)))

    if _signal_running_instance(show=not args.startup):
        return 0
    local = QLocalServer(app)
    QLocalServer.removeServer(SERVER_NAME)
    local.listen(SERVER_NAME)

    data_dir = args.data_dir or default_data_dir()
    _install_error_log(data_dir / "error.log")
    _append(data_dir / "launch.log", f"{time.strftime('%Y-%m-%d %H:%M:%S')} v{__version__} "
                                     f"{'AT-STARTUP' if args.startup else 'manual'} exe={sys.executable}\n", 100_000)

    desk = Desktop(app, data_dir, start_hidden=args.startup)

    def on_connection() -> None:
        conn = local.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: (conn.readAll(), desk.win.show_and_raise()))

    local.newConnection.connect(on_connection)
    app.aboutToQuit.connect(desk.shutdown)
    return app.exec()
