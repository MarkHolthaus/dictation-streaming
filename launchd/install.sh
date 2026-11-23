#!/bin/bash
# Installs the engine LaunchAgent (starts the engine at login). Pass "nightly" to also install the
# 3:30 AM learning job. Needs the venv from the README and a normal (unsandboxed) shell.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents"
mkdir -p "$DEST" "$HOME/Library/Logs"
jobs=(engine)
[ "${1:-}" = "nightly" ] && jobs+=(nightly)
for j in "${jobs[@]}"; do
  out="$DEST/local.dictation.$j.plist"
  sed -e "s|__REPO__|$REPO|g" -e "s|__HOME__|$HOME|g" "$REPO/launchd/local.dictation.$j.plist" > "$out"
  launchctl unload "$out" 2>/dev/null || true
  launchctl load "$out"
  echo "loaded $out"
done
