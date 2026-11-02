"""Self-update for git installs.

When the app quits it starts this module as a detached background
process, which runs ``git pull --ff-only`` in the install folder and
reinstalls dependencies only if ``requirements.txt`` changed. The next
launch runs the new version. With ``--restart`` it relaunches the app
afterwards.

Nothing happens for the packaged .exe or a folder that is not a git clone.
Local edits are never overwritten: a pull that cannot fast-forward is
skipped and logged.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = 0x08000000           # CREATE_NO_WINDOW
_DETACHED = 0x00000008            # DETACHED_PROCESS
MAX_LOG_BYTES = 100_000


def repo_root() -> Path | None:
    """The git checkout the app runs from, or None (frozen exe / zip copy)."""
    if getattr(sys, "frozen", False):
        return None
    root = Path(__file__).resolve().parent.parent
    return root if (root / ".git").exists() else None


def git_available() -> bool:
    return shutil.which("git") is not None


def gui_python() -> str:
    """pythonw.exe on Windows so no console window flashes up."""
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").exists():
        return str(exe.with_name("pythonw.exe"))
    return str(exe)


def _hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _run(args: list[str], cwd: Path, log) -> int:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")   # never hang waiting for a password
    try:
        proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=300,
                              env=env, creationflags=_NO_WINDOW if IS_WINDOWS else 0)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"$ {' '.join(args)}\n  failed: {exc}")
        return 1
    out = (proc.stdout + proc.stderr).strip()
    log(f"$ {' '.join(args)}  ->  exit {proc.returncode}" + (f"\n  {out}" if out else ""))
    return proc.returncode


def run_update(root: Path, log_path: Path | None = None, python: str | None = None) -> str:
    """Pull the latest version. Returns 'updated', 'current' or 'failed'."""
    lines: list[str] = []

    def log(msg: str) -> None:
        lines.append(msg)

    log(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} update in {root}")
    before_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True,
                                 text=True, creationflags=_NO_WINDOW if IS_WINDOWS else 0).stdout.strip()
    req_before = _hash(root / "requirements.txt")
    result = "failed"
    if _run(["git", "pull", "--ff-only", "--quiet"], root, log) == 0:
        after_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True,
                                    text=True, creationflags=_NO_WINDOW if IS_WINDOWS else 0).stdout.strip()
        result = "updated" if after_head != before_head else "current"
        if result == "updated" and _hash(root / "requirements.txt") != req_before:
            py = python or sys.executable
            _run([py, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"], root, log)
    log(f"result: {result}")
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
                log_path.write_text(log_path.read_text(encoding="utf-8")[-MAX_LOG_BYTES // 2:], encoding="utf-8")
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass
    return result


def spawn_update(log_path: Path, restart: bool = False) -> bool:
    """Start the updater in the background, detached from this process."""
    root = repo_root()
    if root is None or not git_available():
        return False
    args = [gui_python(), "-m", "desktop.updater", "--log", str(log_path),
            "--wait-pid", str(os.getpid())]
    if restart:
        args.append("--restart")
    flags = (_DETACHED | _NO_WINDOW | 0x00000200) if IS_WINDOWS else 0   # + NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, cwd=str(root), close_fds=True, creationflags=flags,
                         start_new_session=not IS_WINDOWS,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


def _pid_alive(pid: int) -> bool:
    if IS_WINDOWS:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102   # WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path)
    ap.add_argument("--wait-pid", type=int, default=0)
    ap.add_argument("--restart", action="store_true")
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    deadline = time.time() + 20
    while args.wait_pid and _pid_alive(args.wait_pid) and time.time() < deadline:
        time.sleep(0.25)   # let the app finish closing first
    run_update(root, args.log, python=sys.executable)
    if args.restart:
        subprocess.Popen([gui_python(), str(root / "run_journal_scope.py")], cwd=str(root), close_fds=True,
                         creationflags=(_DETACHED | 0x00000200) if IS_WINDOWS else 0,
                         start_new_session=not IS_WINDOWS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
