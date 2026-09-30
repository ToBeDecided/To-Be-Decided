#!/bin/bash
# Internship Matcher launcher for macOS (also works on Linux).
#
#   Double-click this file in Finder, or run ./start.command in Terminal.
#   ./start.command --check     set everything up, run a self-check, and exit
#
# The first run sets up a private Python environment in .venv (a few minutes);
# later runs start in seconds. macOS ships Python 3.9, which is too old, so if
# no Python 3.10+ is installed this script offers to install "uv", a small tool
# that downloads a private copy of Python for this app. No admin password needed.

set -Eeuo pipefail
cd "$(dirname "$0")"
APP_DIR="$(pwd)"
VENV="$APP_DIR/.venv"
STAMP="$VENV/.internmatch-installed"
MODE="serve"
if [[ "${1:-}" == "--check" ]]; then
  MODE="check"
  shift
fi

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
fail() {
  printf '\n\033[31m%s\033[0m\n' "$*" >&2
  if [[ -t 0 && "$MODE" == "serve" ]]; then read -r -p "Press Return to close this window." _; fi
  exit 1
}
trap 'fail "Setup stopped because of the error above. If it keeps happening, run ./start.command --check and share the output."' ERR

python_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }

find_python() {
  local cand path
  for cand in python3.13 python3.12 python3.11 python3.10 \
      /opt/homebrew/bin/python3 /usr/local/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/3.1{3,2,1,0}/bin/python3 python3; do
    path="$(command -v "$cand" 2>/dev/null || true)"
    [[ -z "$path" ]] && continue
    # Apple's /usr/bin/python3 is 3.9 and pops up an install dialog if the developer tools are missing.
    [[ "$(uname)" == "Darwin" && "$path" == "/usr/bin/python3" ]] && continue
    if python_ok "$path"; then echo "$path"; return 0; fi
  done
  return 1
}

find_uv() {
  local cand
  for cand in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if command -v "$cand" >/dev/null 2>&1; then command -v "$cand"; return 0; fi
  done
  return 1
}

install_uv() {
  bold "Internship Matcher needs Python 3.10 or newer, and this Mac doesn't have it yet."
  echo "I can install 'uv' (from astral.sh), which downloads a private copy of Python just for this app."
  echo "It goes in your home folder and doesn't need an admin password."
  if [[ -t 0 ]]; then
    read -r -p "Install it now? [Y/n] " answer
    [[ "${answer:-y}" =~ ^[Nn] ]] && fail "Okay. Install Python 3.12 from https://www.python.org/downloads/ and run this again."
  fi
  command -v curl >/dev/null 2>&1 || fail "curl is missing, so uv can't be downloaded. Install Python 3.12 from python.org instead."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  UV="$(find_uv)" || fail "uv didn't install correctly."
}

UV="$(find_uv || true)"

# 1. A private Python environment for the app.
if [[ ! -x "$VENV/bin/python" ]] || ! python_ok "$VENV/bin/python"; then
  bold "Setting up Internship Matcher (first run only)..."
  rm -rf "$VENV"
  PY="$(find_python || true)"
  if [[ -n "$UV" ]]; then
    "$UV" venv --quiet --python 3.12 "$VENV"
  elif [[ -n "$PY" ]]; then
    echo "Using $PY"
    "$PY" -m venv "$VENV"
  else
    install_uv
    "$UV" venv --quiet --python 3.12 "$VENV"
  fi
fi

# 2. Install or update the app whenever its requirements change.
WANT="$(cksum < pyproject.toml | cut -d' ' -f1)"
if [[ ! -f "$STAMP" || "$(cat "$STAMP")" != "$WANT" ]]; then
  bold "Installing Internship Matcher and its libraries..."
  if [[ -n "$UV" ]]; then
    "$UV" pip install --quiet --python "$VENV/bin/python" -e .
  else
    "$VENV/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -e .
  fi
  echo "$WANT" > "$STAMP"
fi

trap - ERR
if [[ "$MODE" == "check" ]]; then
  exec "$VENV/bin/internmatch" doctor "$@"
fi
bold "Starting Internship Matcher. Your browser will open in a moment."
exec "$VENV/bin/internmatch" serve "$@"
