#!/bin/bash
# Builds release and wraps the binary into build/Dictation.app, then ad-hoc signs it.
# Set BINARY=/path/to/Dictation to skip `swift build` and wrap an existing binary.
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${BINARY:-}" ]; then
  swift build -c release
  BINARY="$(swift build -c release --show-bin-path)/Dictation"
fi

APP="build/Dictation.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BINARY" "$APP/Contents/MacOS/Dictation"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>local.dictation.app</string>
  <key>CFBundleName</key><string>Dictation</string>
  <key>CFBundleDisplayName</key><string>Dictation</string>
  <key>CFBundleExecutable</key><string>Dictation</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Dictation records your voice while you hold Right Option.</string>
</dict>
</plist>
PLIST

# The requirement names the app by identifier, not by code hash, so the Accessibility,
# Input Monitoring and Screen Recording grants survive a rebuild.
codesign --force -s - --identifier local.dictation.app \
  -r='designated => identifier "local.dictation.app"' "$APP"
codesign --verify --verbose=1 "$APP"
echo "Built $(pwd)/$APP"
