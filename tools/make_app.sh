#!/bin/bash
# Builds "Trading Desk.app" for the Dock.
#   tools/make_app.sh                 -> ~/Applications/Trading Desk.app
#   tools/make_app.sh /some/folder    -> /some/folder/Trading Desk.app
# Run it again after moving the project folder: the app remembers where the project is.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Applications}"
APP="$DEST/Trading Desk.app"
CONTROL="$HERE/tools/app/desk-control.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

chmod +x "$CONTROL"
mkdir -p "$DEST"

# 1. the AppleScript, with this project's path filled in
ESCAPED="${CONTROL//\\/\\\\}"; ESCAPED="${ESCAPED//\"/\\\"}"
sed "s|__CONTROL__|$ESCAPED|" "$HERE/tools/app/TradingDesk.applescript" >"$WORK/TradingDesk.applescript"
grep -q "__CONTROL__" "$WORK/TradingDesk.applescript" && { echo "path was not filled in" >&2; exit 1; }

# 2. a stay-open app (so Quit from the Dock stops the desk)
rm -rf "$APP"
/usr/bin/osacompile -s -o "$APP" "$WORK/TradingDesk.applescript"

# 3. the icon (tools/app/icon.png; drawing it again with Swift takes many minutes, so only when it is missing)
if [ ! -f "$HERE/tools/app/icon.png" ]; then
  /usr/bin/swift "$HERE/tools/app/make_icon.swift" "$HERE/tools/app/icon.png" >/dev/null
fi
cp "$HERE/tools/app/icon.png" "$WORK/icon.png"
mkdir -p "$WORK/icon.iconset"
for s in 16 32 128 256 512; do
  /usr/bin/sips -z $s $s "$WORK/icon.png" --out "$WORK/icon.iconset/icon_${s}x${s}.png" >/dev/null
  /usr/bin/sips -z $((s * 2)) $((s * 2)) "$WORK/icon.png" --out "$WORK/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns -o "$APP/Contents/Resources/applet.icns" "$WORK/icon.iconset"
# osacompile also ships the default script icon in Assets.car and points CFBundleIconName at it,
# which wins over applet.icns. Remove both so our icon shows.
rm -f "$APP/Contents/Resources/Assets.car"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null || true

# 4. name and id, so macOS shows "Trading Desk" and does not hide the menu-bar name
/usr/libexec/PlistBuddy -c "Set :CFBundleName Trading Desk" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string local.tradingdesk.app" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier local.tradingdesk.app" "$APP/Contents/Info.plist"
# 5. sign again (local "ad-hoc" signature), since the icon and Info.plist changed after osacompile signed it
/usr/bin/codesign --force --deep --sign - "$APP" 2>/dev/null
/usr/bin/codesign --verify --deep --strict "$APP"
touch "$APP"
echo "Built: $APP"
