"""Windows-only helpers: DPAPI token protection and shortcuts.

Everything degrades to a no-op (or plain storage) on other systems so the
app and its tests still run there.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = 0x08000000

if IS_WINDOWS:
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def dpapi_available() -> bool:
    return IS_WINDOWS


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob._buf = buf  # keep alive
    return blob


def dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    out = _Blob()
    if not _crypt32.CryptProtectData(ctypes.byref(_blob(data)), "Journal Scope", ctypes.byref(_blob(entropy)),
                                     None, None, 0x1, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def dpapi_unprotect(data: bytes, entropy: bytes) -> bytes:
    out = _Blob()
    if not _crypt32.CryptUnprotectData(ctypes.byref(_blob(data)), None, ctypes.byref(_blob(entropy)),
                                       None, None, 0x1, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


# ------------------------------------------------------------- shortcuts
def gui_python() -> str:
    """pythonw.exe on Windows so no console window appears."""
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").exists():
        return str(exe.with_name("pythonw.exe"))
    return str(exe)


def launcher() -> Path:
    return Path(__file__).resolve().parent.parent / "run_journal_scope.py"


def startup_shortcut() -> Path | None:
    """The per-user Startup folder (fixed location; no PowerShell, so it is instant)."""
    if not IS_WINDOWS:
        return None
    base = os.environ.get("APPDATA")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Journal Scope.lnk" if base else None


def desktop_shortcut() -> Path | None:
    """The visible Desktop, following OneDrive redirection (Qt asks Windows for the known folder)."""
    if not IS_WINDOWS:
        return None
    from PySide6.QtCore import QStandardPaths
    path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    return Path(path) / "Journal Scope.lnk" if path else None


def create_shortcut(lnk: Path, args: str = "") -> None:
    icon = Path(__file__).resolve().parent.parent / "app" / "static" / "icon.ico"
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:JS_LNK);"
              "$s.TargetPath=$env:JS_EXE; $s.Arguments=$env:JS_ARGS; $s.WorkingDirectory=$env:JS_DIR;"
              "if (Test-Path $env:JS_ICON) { $s.IconLocation=$env:JS_ICON };"
              "$s.Description='Journal Scope'; $s.Save()")
    env = dict(os.environ, JS_LNK=str(lnk), JS_EXE=gui_python(), JS_DIR=str(launcher().parent),
               JS_ARGS=f'"{launcher()}" {args}'.strip(), JS_ICON=str(icon))
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-Command", script], env=env, capture_output=True, text=True, timeout=30,
                          creationflags=_NO_WINDOW)
    if proc.returncode != 0 or not lnk.exists():
        raise OSError(proc.stderr.strip() or "Could not create the shortcut")


def start_with_windows_enabled() -> bool:
    lnk = startup_shortcut()
    return bool(lnk and lnk.exists())


def set_start_with_windows(enabled: bool) -> None:
    lnk = startup_shortcut()
    if lnk is None:
        raise OSError("Only available on Windows")
    if enabled:
        create_shortcut(lnk, "--startup")
    elif lnk.exists():
        lnk.unlink()
