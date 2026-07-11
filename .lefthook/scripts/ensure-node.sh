#!/usr/bin/env bash
# Source at the top of hook scripts that shell out to npm/node (oxlint, tsc,
# jest). Git hooks run with the shell's default PATH, which on a machine
# using nvm may resolve `node` to an old system/default version too old for
# these tools (oxlint needs a modern Node) even though an interactive shell
# would pick up a newer one via `nvm use`. CI pins node-version: 20
# (.github/workflows/ci.yml) — match that here so local and CI results agree.
set -euo pipefail

_node_major() {
  node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'
}

if ! command -v node >/dev/null 2>&1 || [ "$(_node_major)" -lt 16 ] 2>/dev/null; then
  if [ -s "$HOME/.nvm/nvm.sh" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.nvm/nvm.sh"
    nvm use 20 >/dev/null 2>&1 || nvm use --lts >/dev/null 2>&1 || true
  fi
fi

if ! command -v node >/dev/null 2>&1 || [ "$(_node_major)" -lt 16 ] 2>/dev/null; then
  echo "hook: node >=16 required (found $(node --version 2>/dev/null || echo 'none')); run 'nvm use 20' or install Node 20." >&2
  exit 1
fi
