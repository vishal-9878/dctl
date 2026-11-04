#!/usr/bin/env bash
# Build dist/dctl-<version>-macos-<arch>.pkg on macOS. Needs: python3 with pyinstaller, Xcode command line tools.
#   packaging/macos/build-pkg.sh [version]
set -euo pipefail
cd "$(dirname "$0")/../.."
VER="${1:-$(python3 -c 'import re;print(re.search(r"__version__ = \"(.+?)\"",open("src/dctl/__init__.py").read())[1])')}"
VER="${VER#v}"; ARCH=$(uname -m)
B=build/mac; ROOT=$B/root; rm -rf "$B"; mkdir -p "$B" dist

# 1) standalone dctl-ui binary (Python bundled inside)
python3 -m PyInstaller --noconfirm --onefile --name dctl-ui --paths src \
  --distpath "$B/bin" --workpath "$B/work" --specpath "$B" packaging/launcher.py

# 2) payload: /usr/local/lib/dctl, /usr/local/bin/{dctl,dctl-ui}, /Applications/dctl.app
install -d "$ROOT/usr/local/lib/dctl" "$ROOT/usr/local/bin"
install -m755 "$B/bin/dctl-ui" "$ROOT/usr/local/lib/dctl/dctl-ui"
install -m755 src/dctl/dctl.sh "$ROOT/usr/local/lib/dctl/dctl.sh"
ln -s ../lib/dctl/dctl-ui "$ROOT/usr/local/bin/dctl-ui"
printf '#!/bin/sh\nexec bash /usr/local/lib/dctl/dctl.sh "$@"\n' > "$ROOT/usr/local/bin/dctl"; chmod 755 "$ROOT/usr/local/bin/dctl"

APP="$ROOT/Applications/dctl.app/Contents"
install -d "$APP/MacOS" "$APP/Resources"
cat > "$APP/MacOS/dctl" <<'S'
#!/bin/sh
# Opens dctl in a Terminal window (shows the address; close the window to stop dctl).
# If dctl is already running, this just opens the dashboard in the browser.
exec open -a Terminal /usr/local/bin/dctl-ui
S
chmod 755 "$APP/MacOS/dctl"
ICONSET="$B/dctl.iconset"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s packaging/icon.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2)); [ $d -le 512 ] && sips -z $d $d packaging/icon.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
cp packaging/icon.png "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o "$APP/Resources/dctl.icns"
cat > "$APP/Info.plist" <<S
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>dctl</string>
  <key>CFBundleDisplayName</key><string>dctl</string>
  <key>CFBundleIdentifier</key><string>io.github.vishal-9878.dctl</string>
  <key>CFBundleVersion</key><string>$VER</string>
  <key>CFBundleShortVersionString</key><string>$VER</string>
  <key>CFBundleExecutable</key><string>dctl</string>
  <key>CFBundleIconFile</key><string>dctl</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict></plist>
S

# 3) the installer package (app must not be relocated to another copy the user may have)
pkgbuild --analyze --root "$ROOT" "$B/component.plist" >/dev/null
/usr/libexec/PlistBuddy -c "Set :0:BundleIsRelocatable false" "$B/component.plist"
OUT="dist/dctl-${VER}-macos-${ARCH}.pkg"
pkgbuild --root "$ROOT" --component-plist "$B/component.plist" --identifier io.github.vishal-9878.dctl \
  --version "$VER" --install-location / "$OUT" >/dev/null
echo "$OUT"
