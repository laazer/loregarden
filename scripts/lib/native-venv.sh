#!/usr/bin/env bash
# Sourced: rebuild server/.venv natively when it holds a translated Python.
#
# On Apple silicon with an Intel Homebrew in /usr/local, `uv venv` can pick that
# Python, and everything the venv runs then goes through Rosetta: twice the cpu
# on the same work (1.6s against 0.8s, 2026-11-24), for the server and for every
# hook and `capacity run` that borrows its venv. `uv sync` keeps whichever
# interpreter it finds, so nothing ever corrects it.
#
# Call it only while nothing is serving from the venv. The old one is kept until
# the new one is ready, and put back if it cannot be built.

_venv_machine() {
  "$1/bin/python" -c 'import platform; print(platform.machine())' 2>/dev/null
}

ensure_native_venv() {
  local server="$1" venv="$1/.venv" version old
  [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] || return 0
  [ "$(_venv_machine "$venv")" = "x86_64" ] || return 0

  version="$("$venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" || return 0
  old="$server/.venv.translated"
  echo "server/.venv runs Python $version through Rosetta; rebuilding it natively." >&2
  rm -rf "$old"
  mv "$venv" "$old" || return 1
  if uv venv --python "cpython-$version-macos-aarch64" "$venv" >&2 \
    && (cd "$server" && uv sync >&2) \
    && [ "$(_venv_machine "$venv")" = "arm64" ]; then
    rm -rf "$old"
    return 0
  fi
  echo "Could not build a native venv (uv's output is above); keeping the translated one." >&2
  rm -rf "$venv"
  mv "$old" "$venv"
}
