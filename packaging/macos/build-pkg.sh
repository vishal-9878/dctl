#!/usr/bin/env bash
# Build dist/dctl-<version>-macos-<arch>.pkg on macOS.
# Needs: python3 with pyinstaller + pywebview, Xcode command line tools.
#   packaging/macos/build-pkg.sh [version]
set -euo pipefail
cd "$(dirname "$0")/../.."
VER="${1:-$(python3 -c 'import re;print(re.search(r"__version__ = \"(.+?)\"",open("src/dctl/__init__.py").read())[1])')}"
VER="${VER#v}"; ARCH=$(uname -m)
B=build/mac; ROOT=$B/root; rm -rf "$B"; mkdir -p "$B" dist

# 1) app icon (.icns) from the 1024 px PNG
ICONSET="$B/dctl.iconset"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s packaging/icon.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) packaging/icon.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$B/dctl.icns"

# 2) dctl.app: a real windowed app (Python + pywebview bundled inside)
python3 -m PyInstaller --noconfirm --windowed --name dctl --icon "$B/dctl.icns" --exclude-module pkg_resources \
  --osx-bundle-identifier io.github.vishal-9878.dctl --paths src --add-data "src/dctl/icon.png:dctl" \
  --distpath "$B/dist" --workpath "$B/work" packaging/launcher.py
APPB="$B/dist/dctl.app"
PL="$APPB/Contents/Info.plist"
pb() { /usr/libexec/PlistBuddy -c "$1" "$PL" 2>/dev/null || true; }
pb "Set :CFBundleShortVersionString $VER"; pb "Add :CFBundleShortVersionString string $VER"
pb "Set :CFBundleVersion $VER"; pb "Add :CFBundleVersion string $VER"
pb "Add :LSApplicationCategoryType string public.app-category.developer-tools"
pb "Add :NSHighResolutionCapable bool true"
pb "Add :NSAppTransportSecurity dict"; pb "Add :NSAppTransportSecurity:NSAllowsLocalNetworking bool true"
codesign --force --deep --sign - "$APPB"          # ad-hoc signature (needed on Apple Silicon after editing the plist)

# 3) payload: /Applications/dctl.app, /usr/local/bin/{dctl-ui,dctl}, /usr/local/lib/dctl/dctl.sh
install -d "$ROOT/Applications" "$ROOT/usr/local/lib/dctl" "$ROOT/usr/local/bin"
cp -R "$APPB" "$ROOT/Applications/"
install -m755 src/dctl/dctl.sh "$ROOT/usr/local/lib/dctl/dctl.sh"
ln -s /Applications/dctl.app/Contents/MacOS/dctl "$ROOT/usr/local/bin/dctl-ui"
printf '#!/bin/sh\nexec bash /usr/local/lib/dctl/dctl.sh "$@"\n' > "$ROOT/usr/local/bin/dctl"; chmod 755 "$ROOT/usr/local/bin/dctl"

# 4) installer package (don't relocate the app onto another copy the user may have elsewhere)
pkgbuild --analyze --root "$ROOT" "$B/component.plist" >/dev/null
/usr/libexec/PlistBuddy -c "Set :0:BundleIsRelocatable false" "$B/component.plist"
OUT="dist/dctl-${VER}-macos-${ARCH}.pkg"
pkgbuild --root "$ROOT" --component-plist "$B/component.plist" --identifier io.github.vishal-9878.dctl \
  --version "$VER" --install-location / "$OUT" >/dev/null
echo "$OUT"
