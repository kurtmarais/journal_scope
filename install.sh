#!/usr/bin/env bash
# Journal Scope - macOS / Linux setup. Run once:  bash install.sh
# Creates a desktop shortcut (macOS: "Journal Scope.app", Linux: .desktop entry).
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"

PY="$(command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10+ is required (https://www.python.org/downloads/)." >&2
  exit 1
fi

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null
echo "Installing packages..."
.venv/bin/pip install -r requirements.txt
echo "Installing Chromium for page rendering..."
.venv/bin/python -m playwright install chromium

DESKTOP="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
mkdir -p "$DESKTOP"

if [ "$(uname)" = "Darwin" ]; then
  APP="$DESKTOP/Journal Scope.app"
  mkdir -p "$APP/Contents/MacOS"
  cat > "$APP/Contents/MacOS/journal-scope" <<EOF
#!/bin/bash
cd "$DIR" && exec "$DIR/.venv/bin/python" -m app.desktop
EOF
  chmod +x "$APP/Contents/MacOS/journal-scope"
  cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Journal Scope</string>
  <key>CFBundleExecutable</key><string>journal-scope</string>
  <key>CFBundleIdentifier</key><string>local.journal-scope</string>
  <key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
EOF
  echo "Done. Open \"Journal Scope\" from your Desktop (first time: right-click > Open)."
else
  ENTRY="$DESKTOP/journal-scope.desktop"
  cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=Journal Scope
Comment=Check a journal's credibility and publication details
Exec=bash -c 'cd "$DIR" && "$DIR/.venv/bin/python" -m app.desktop'
Terminal=false
Categories=Education;Office;
EOF
  chmod +x "$ENTRY"
  mkdir -p "$HOME/.local/share/applications"
  cp "$ENTRY" "$HOME/.local/share/applications/journal-scope.desktop"
  echo "Done. Open \"Journal Scope\" from your desktop or applications menu."
  echo "If it opens in your browser instead of its own window: sudo apt install python3-gi gir1.2-webkit2-4.1"
fi
