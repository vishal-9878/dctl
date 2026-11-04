#!/bin/sh
# Build a signed, flat APT repository from .deb files (used by the release workflow for GitHub Pages).
#
#   packaging/apt/build-repo.sh <folder with .deb files> <output site folder>
#
# The signing key must already be in gpg (APT_KEY_ID picks one if there are several).
# Output:  <site>/apt/{*.deb,Packages,Packages.gz,Release,InRelease,Release.gpg}, <site>/dctl.gpg, <site>/index.html
set -eu
command -v apt-ftparchive >/dev/null || { echo "apt-ftparchive not found: sudo apt-get install apt-utils" >&2; exit 1; }
DEBS=$1; SITE=$2
KEY=${APT_KEY_ID:-$(gpg --batch --list-secret-keys --with-colons | awk -F: '$1=="fpr"{print $10; exit}')}
[ -n "$KEY" ] || { echo "no gpg secret key found" >&2; exit 1; }
URL=${APT_URL:-https://vishal-9878.github.io/dctl}

rm -rf "$SITE"; mkdir -p "$SITE/apt"
cp "$DEBS"/*.deb "$SITE/apt/"
cd "$SITE/apt"
apt-ftparchive packages . > Packages
gzip -9kn Packages
apt-ftparchive -o APT::FTPArchive::Release::Origin=dctl -o APT::FTPArchive::Release::Label=dctl \
  -o APT::FTPArchive::Release::Suite=stable -o APT::FTPArchive::Release::Codename=stable \
  -o "APT::FTPArchive::Release::Description=dctl desktop app" release . > Release
gpg --batch --yes --local-user "$KEY" --clearsign -o InRelease Release
gpg --batch --yes --local-user "$KEY" -abs -o Release.gpg Release
cd ..
gpg --batch --yes --export "$KEY" > dctl.gpg          # binary keyring for apt's signed-by=
touch .nojekyll
cat > index.html <<HTML
<!doctype html><meta charset="utf-8"><title>dctl APT repository</title>
<style>body{font:15px/1.6 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px}pre{background:#f4f4f6;padding:12px;border-radius:8px;overflow:auto}</style>
<h1>dctl APT repository</h1>
<p>Install <a href="https://github.com/vishal-9878/dctl">dctl</a> on Ubuntu / Debian and get updates with <code>apt upgrade</code>:</p>
<pre>curl -fsSL $URL/dctl.gpg | sudo tee /usr/share/keyrings/dctl.gpg &gt;/dev/null
echo "deb [signed-by=/usr/share/keyrings/dctl.gpg] $URL/apt ./" | sudo tee /etc/apt/sources.list.d/dctl.list
sudo apt update &amp;&amp; sudo apt install dctl</pre>
HTML
echo "built $SITE ($(ls apt/*.deb | wc -l) package(s), key $KEY)"
