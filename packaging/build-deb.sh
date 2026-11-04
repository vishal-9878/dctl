#!/usr/bin/env bash
# Build dist/dctl_<version>_all.deb  (Ubuntu / Debian). Needs: dpkg-deb.
#   packaging/build-deb.sh [version]
set -euo pipefail
cd "$(dirname "$0")/.."
VER="${1:-$(python3 -c 'import re;print(re.search(r"__version__ = \"(.+?)\"",open("src/dctl/__init__.py").read())[1])')}"
VER="${VER#v}"
ROOT=$(mktemp -d); chmod 755 "$ROOT"; trap 'rm -rf "$ROOT"' EXIT

install -d "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/lib/dctl/dctl" "$ROOT/usr/share/applications" "$ROOT/usr/share/doc/dctl"
install -m644 src/dctl/*.py src/dctl/icon.png "$ROOT/usr/lib/dctl/dctl/"
# bundle pywebview (pure Python on Linux; it drives the system GTK / WebKitGTK through python3-gi)
python3 -m pip install --quiet --no-compile --no-deps --target "$ROOT/usr/lib/dctl/vendor" \
  "pywebview==6.1" "bottle==0.13.2" "proxy_tools==0.1.0" "typing_extensions==4.12.2"
rm -rf "$ROOT/usr/lib/dctl/vendor/bin" "$ROOT"/usr/lib/dctl/vendor/*.dist-info/RECORD
install -m755 src/dctl/dctl.sh "$ROOT/usr/lib/dctl/dctl/"
sed -i "s/^__version__ = .*/__version__ = \"$VER\"/" "$ROOT/usr/lib/dctl/dctl/__init__.py"

cat > "$ROOT/usr/bin/dctl-ui" <<'S'
#!/bin/sh
PYTHONPATH=/usr/lib/dctl:/usr/lib/dctl/vendor exec python3 -m dctl.ui "$@"
S
cat > "$ROOT/usr/bin/dctl" <<'S'
#!/bin/sh
PYTHONPATH=/usr/lib/dctl:/usr/lib/dctl/vendor exec python3 -m dctl.cli "$@"
S
chmod 755 "$ROOT/usr/bin/dctl-ui" "$ROOT/usr/bin/dctl"

cat > "$ROOT/usr/share/applications/dctl.desktop" <<'S'
[Desktop Entry]
Type=Application
Name=dctl
GenericName=Docker & PM2 dashboard
Comment=Manage Docker and PM2 on many servers over SSH
Exec=dctl-ui
Icon=dctl
Terminal=false
StartupWMClass=dctl
StartupNotify=true
Categories=System;Monitor;
Keywords=docker;pm2;ssh;servers;containers;
S
for s in 16 24 32 48 64 128 256 512; do
  install -Dm644 "packaging/icons/$s.png" "$ROOT/usr/share/icons/hicolor/${s}x${s}/apps/dctl.png"
done
install -m644 README.md "$ROOT/usr/share/doc/dctl/README.md"
install -m644 LICENSE "$ROOT/usr/share/doc/dctl/copyright"

SIZE=$(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<S
Package: dctl
Version: $VER
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), openssh-client, python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0
Recommends: fzf
Installed-Size: $SIZE
Maintainer: Vishal Singh <vishal-9878@users.noreply.github.com>
Homepage: https://github.com/vishal-9878/dctl
Description: Agentless Docker & PM2 dashboard for many servers over SSH
 dctl runs on your machine and manages Docker containers, compose files,
 PM2 processes, files, disk space and systemd services on many servers.
 The servers need nothing new: only SSH and the Docker / PM2 they already have.
 Open "dctl" from the applications menu (or run "dctl-ui"), or use the
 "dctl" command line tool.
S

mkdir -p dist
OUT="dist/dctl_${VER}_all.deb"
dpkg-deb --build --root-owner-group "$ROOT" "$OUT" >/dev/null
echo "$OUT"
