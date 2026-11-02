"""Launcher used by the desktop shortcut and the Windows startup shortcut.

The app runs without a console (pythonw), so a crash while starting up would
otherwise vanish without a trace: record it and show a message instead.
"""

import os
import sys
import time
import traceback
from pathlib import Path


def _crash_report() -> None:
    text = traceback.format_exc()
    base = os.environ.get("JOURNAL_SCOPE_DATA_DIR") or os.path.join(
        os.environ.get("APPDATA") or str(Path.home()), "JournalScope")
    log = Path(base) / "startup-crash.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} argv={sys.argv} exe={sys.executable}\n{text}\n")
    except OSError:
        pass
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, f"Journal Scope could not start.\n\nDetails were saved to:\n{log}\n\n{text[-700:]}", "Journal Scope", 0x10)


def _note_process_start() -> None:
    """First thing on launch, before Qt loads: proves whether Windows started
    the app at all, even if it then dies too early to report anything."""
    base = os.environ.get("JOURNAL_SCOPE_DATA_DIR") or os.path.join(
        os.environ.get("APPDATA") or str(Path.home()), "JournalScope")
    try:
        Path(base).mkdir(parents=True, exist_ok=True)
        with open(os.path.join(base, "launch.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} process started args={sys.argv[1:]}\n")
    except OSError:
        pass


if __name__ == "__main__":
    os.environ["JOURNAL_SCOPE_T0"] = str(time.time())   # start-up timing in launch.log
    _note_process_start()
    try:
        from desktop.app import main
        code = main()
    except SystemExit:
        raise
    except BaseException:
        _crash_report()
        code = 1
    sys.exit(code)
