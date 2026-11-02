"""Background sync thread and the "Phone sync" dialog."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout)

from .sync import AuthError, SyncEngine, SyncError

INTERVAL = 20          # seconds between checks for phone requests
INTERVAL_IDLE = 60     # after an error


class SyncController(QObject):
    status_changed = Signal(str)
    reports_changed = Signal()

    def __init__(self, engine: SyncEngine):
        super().__init__()
        self.engine = engine
        self.status = "Phone sync off" if not engine.configured else "Phone sync starting"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._busy = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="journal-scope-sync", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def kick(self) -> None:
        """Sync soon (after a lookup or delete on the PC)."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _set(self, text: str) -> None:
        self.status = text
        self.status_changed.emit(text)

    def run_once(self) -> None:
        if not self.engine.configured:
            self._set("Phone sync off")
            return
        with self._busy:
            try:
                result = self.engine.cycle()
                if result["downloaded"] or result["looked_up"]:
                    self.reports_changed.emit()
                note = f" ({result['looked_up']} phone lookup{'s' if result['looked_up'] != 1 else ''})" if result["looked_up"] else ""
                lists = result.get("lists")
                if lists:
                    failed = [k for k, v in lists.items() if not v.startswith("ok")]
                    note += f"; phone lists: {'reloaded' if not failed else 'failed for ' + ', '.join(failed)}"
                self._set(f"Synced {time.strftime('%H:%M')}{note}")
            except AuthError as exc:
                self._set(f"Phone sync: sign in again ({exc})")
            except SyncError as exc:
                self._set(f"Phone sync failed: {exc}")

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            wait = INTERVAL if not self.status.startswith("Phone sync failed") else INTERVAL_IDLE
            self._wake.wait(wait)
            self._wake.clear()


class SyncDialog(QDialog):
    def __init__(self, controller: SyncController, parent=None):
        super().__init__(parent)
        self.controller = controller
        engine = controller.engine
        self.setWindowTitle("Phone sync")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        intro = QLabel("Reports sync to your own Supabase project so the phone app can read them, "
                       "and lookups asked for on the phone run on this PC. See docs/phone-setup.md.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.url = QLineEdit(engine.settings.get("sync_url", ""))
        self.url.setPlaceholderText("https://<project>.supabase.co")
        self.key = QLineEdit(engine.settings.get("sync_key", ""))
        self.key.setPlaceholderText("sb_publishable_…")
        self.email = QLineEdit(engine.settings.get("sync_email", ""))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("used once to sign in; not stored")
        form.addRow("Project URL", self.url)
        form.addRow("Publishable key", self.key)
        form.addRow("Email", self.email)
        form.addRow("Password", self.password)
        lay.addLayout(form)
        self.state = QLabel(controller.status)
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        buttons = QDialogButtonBox()
        self.sign_in_btn = QPushButton("Sign in and sync")
        self.sign_in_btn.setDefault(True)
        self.sign_out_btn = QPushButton("Sign out")
        self.sign_out_btn.setEnabled(engine.configured)
        buttons.addButton(self.sign_in_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.sign_out_btn, QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.sign_in_btn.clicked.connect(self._sign_in)
        self.sign_out_btn.clicked.connect(self._sign_out)
        lay.addWidget(buttons)
        controller.status_changed.connect(self.state.setText)

    def _sign_in(self) -> None:
        url, key = self.url.text().strip(), self.key.text().strip()
        email, password = self.email.text().strip(), self.password.text()
        if not (url.startswith("https://") and key and email and password):
            QMessageBox.warning(self, "Phone sync", "Fill in the project URL (https://…), publishable key, email and password.")
            return
        if "service_role" in key or key.startswith("sb_secret_"):
            QMessageBox.warning(self, "Phone sync", "That is a secret key. Use the publishable key.")
            return
        try:
            who = self.controller.engine.sign_in(url, key, email, password)
        except SyncError as exc:
            QMessageBox.warning(self, "Phone sync", f"Sign-in failed: {exc}")
            return
        self.password.clear()
        self.sign_out_btn.setEnabled(True)
        self.state.setText(f"Signed in as {who}. Syncing…")
        self.controller.kick()

    def _sign_out(self) -> None:
        self.controller.engine.sign_out()
        self.sign_out_btn.setEnabled(False)
        self.state.setText("Signed out. Reports stay on this PC and in Supabase.")
        self.controller.kick()
