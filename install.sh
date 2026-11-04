#!/bin/sh
# dctl installer for Linux and macOS. No sudo needed.
#
#   curl -fsSL https://raw.githubusercontent.com/vishal-9878/dctl/main/install.sh | sh
#
# Installs with pipx if you have it, otherwise into its own virtualenv in
# ~/.local/share/dctl and links `dctl` and `dctl-ui` into ~/.local/bin.
# Uninstall:  pipx uninstall dctl   or   rm -rf ~/.local/share/dctl ~/.local/bin/dctl ~/.local/bin/dctl-ui
set -eu

REPO="https://github.com/vishal-9878/dctl"
SRC="${DCTL_SRC:-$REPO/archive/refs/heads/main.tar.gz}"   # DCTL_SRC can be a local folder or another URL

say()  { printf '\033[1;35m»\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mdctl install: %s\033[0m\n' "$*" >&2; exit 1; }

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then PY=$c; break; fi
done
[ -n "$PY" ] || fail "Python 3.8 or newer is needed. Ubuntu: sudo apt install python3   macOS: brew install python"
command -v ssh >/dev/null 2>&1 || fail "the OpenSSH client (ssh) is needed"

if command -v pipx >/dev/null 2>&1; then
  say "installing with pipx"
  pipx install --force "$SRC"
else
  DATA="${XDG_DATA_HOME:-$HOME/.local/share}/dctl"
  BIN="$HOME/.local/bin"
  say "installing into $DATA"
  rm -rf "$DATA/venv"
  "$PY" -m venv "$DATA/venv" 2>/dev/null || fail "could not create a virtualenv. Ubuntu/Debian: sudo apt install python3-venv  (or install pipx)"
  "$DATA/venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$DATA/venv/bin/python" -m pip install --quiet "$SRC"
  mkdir -p "$BIN"
  ln -sf "$DATA/venv/bin/dctl" "$BIN/dctl"
  ln -sf "$DATA/venv/bin/dctl-ui" "$BIN/dctl-ui"
  case ":$PATH:" in
    *":$BIN:"*) ;;
    *) say "add this line to your ~/.bashrc or ~/.zshrc, then open a new terminal:"
       printf '\n    export PATH="$HOME/.local/bin:$PATH"\n\n' ;;
  esac
fi

say "done. Start the dashboard with:  dctl-ui"
