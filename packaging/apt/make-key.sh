#!/bin/sh
# One-time setup: create the GPG key that signs the dctl APT repository.
#
#   packaging/apt/make-key.sh [output file]      (default: ./dctl-apt-signing-key.asc)
#
# Then on GitHub: repo Settings -> Secrets and variables -> Actions -> New repository secret
#   Name:  APT_GPG_PRIVATE_KEY      Value: the whole content of the output file
# and delete the file afterwards. The key is made in a temporary keyring; your own gpg keys are not touched.
set -eu
OUT=${1:-dctl-apt-signing-key.asc}
[ ! -e "$OUT" ] || { echo "$OUT already exists" >&2; exit 1; }
G=$(mktemp -d); trap 'rm -rf "$G"' EXIT
export GNUPGHOME="$G"
gpg --batch --quiet --passphrase '' --quick-gen-key "dctl APT repository <dctl-apt@users.noreply.github.com>" ed25519 sign 5y
( umask 077; gpg --batch --armor --export-secret-keys > "$OUT" )
gpg --list-keys --keyid-format long | sed -n '/^pub/,/^uid/p'
echo
echo "Private key written to: $OUT"
echo "Add it as the GitHub Actions secret APT_GPG_PRIVATE_KEY, then delete the file (keep a backup somewhere safe if you like)."
