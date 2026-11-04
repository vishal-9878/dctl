#!/usr/bin/env bash
# Build dist/dctl_<version>_all.deb  (Ubuntu / Debian). Needs: dpkg-deb.
#   packaging/build-deb.sh [version]
set -euo pipefail
cd "$(dirname "$0")/.."
VER="${1:-$(python3 -c 'import re;print(re.search(r"__version__ = \"(.+?)\"",open("src/dctl/__init__.py").read())[1])')}"
VER="${VER#v}"
ROOT=$(mktemp -d); chmod 755 "$ROOT"; trap 'rm -rf "$ROOT"' EXIT

install -d "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/lib/dctl/dctl" "$ROOT/usr/share/applications" \
           "$ROOT/usr/share/icons/hicolor/512x512/apps" "$ROOT/usr/share/doc/dctl"
install -m644 src/dctl/*.py "$ROOT/usr/lib/dctl/dctl/"
install -m755 src/dctl/dctl.sh "$ROOT/usr/lib/dctl/dctl/"
sed -i "s/^__version__ = .*/__version__ = \"$VER\"/" "$ROOT/usr/lib/dctl/dctl/__init__.py"

cat > "$ROOT/usr/bin/dctl-ui" <<'S'
#!/bin/sh
PYTHONPATH=/usr/lib/dctl exec python3 -m dctl.ui "$@"
S
cat > "$ROOT/usr/bin/dctl" <<'S'
#!/bin/sh
PYTHONPATH=/usr/lib/dctl exec python3 -m dctl.cli "$@"
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
Terminal=true
Categories=System;Monitor;
Keywords=docker;pm2;ssh;servers;containers;
S
install -m644 packaging/icon.png "$ROOT/usr/share/icons/hicolor/512x512/apps/dctl.png"
install -m644 README.md "$ROOT/usr/share/doc/dctl/README.md"
install -m644 LICENSE "$ROOT/usr/share/doc/dctl/copyright"

SIZE=$(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<S
Package: dctl
Version: $VER
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), openssh-client
Recommends: fzf
Installed-Size: $SIZE
Maintainer: Vishal Singh <vishal-9878@users.noreply.github.com>
Homepage: https://github.com/vishal-9878/dctl
Description: Agentless Docker & PM2 dashboard for many servers over SSH
 dctl runs on your machine and manages Docker containers, compose files,
 PM2 processes, files, disk space and systemd services on many servers.
 The servers need nothing new: only SSH and the Docker / PM2 they already have.
 Start the web dashboard with "dctl-ui", or use the "dctl" command line tool.
S

mkdir -p dist
OUT="dist/dctl_${VER}_all.deb"
dpkg-deb --build --root-owner-group "$ROOT" "$OUT" >/dev/null
echo "$OUT"
