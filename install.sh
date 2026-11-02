#!/bin/bash
# Build SetBuilder.app from this repository and install it in ~/Applications.
#   ./install.sh                 install or update
#   ./install.sh --with-claude   also install Claude Code (to use a Claude Pro/Max subscription)
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/SetBuilder.app"
C="$APP/Contents"

[[ "$(uname)" == "Darwin" ]] || { echo "SetBuilder runs on macOS only."; exit 1; }

PY=""
for p in /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$(command -v "$p")"; break
  fi
done
[[ -n "$PY" ]] || { echo "Python 3.11+ is required: brew install python"; exit 1; }
echo "Python: $PY"

mkdir -p "$C/MacOS" "$C/Resources"
cp "$REPO/packaging/Info.plist" "$C/Info.plist"
cp "$REPO/packaging/SetBuilder" "$C/MacOS/SetBuilder"
cp "$REPO/packaging/AppIcon.icns" "$C/Resources/AppIcon.icns"
chmod +x "$C/MacOS/SetBuilder"
rsync -a --delete --exclude __pycache__ "$REPO/app/" "$C/Resources/app/"

VENV="$C/Resources/venv"
if [[ ! -x "$VENV/bin/python" ]] || ! "$VENV/bin/python" -c 'import sys' 2>/dev/null; then
  rm -rf "$VENV"
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO/requirements.txt"

if [[ "${1:-}" == "--with-claude" ]] && ! command -v claude >/dev/null 2>&1; then
  brew install --cask claude-code
fi

command -v ffmpeg >/dev/null 2>&1 || echo "Tip: brew install ffmpeg (not required, but useful for odd formats)."
touch "$APP"
echo "Installed: $APP"
echo "Open it from Finder › Applications (your home folder) or Spotlight: SetBuilder"
