#!/usr/bin/env bash
# Sourced: what the pre-push suites need installed before they can run at all.
#
# One definition with two callers. push-prerequisites.sh asks before the push
# queues for capacity; server-tests.sh and client-tests.sh ask again when they
# start, because `task ci` and land verification run those without the hook.
#
# A push from a worktree without client/node_modules once waited 1h26m and
# 39m39s in the capacity line to be told so (2026-11-23).
PREREQUISITES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# The install a missing tool may already have running: worktree_bootstrap.sh
# takes this lock, and a second `npm ci` beside it breaks both.
_prerequisites_note_bootstrap() {
  local gitdir
  gitdir="$(git -C "$PREREQUISITES_ROOT" rev-parse --absolute-git-dir 2>/dev/null)" || return 0
  if [ -d "$gitdir/worktree-bootstrap.lock" ]; then
    echo "pre-push: this worktree's setup is installing it now; wait for it, then push again (log: $gitdir/worktree-bootstrap.log)." >&2
  fi
}

# jest's own binary, not the directory: an `npm ci` that died part-way leaves
# the directory behind. worktree_bootstrap.sh and registry-checks.sh ask the same.
require_client_modules() {
  if [ -x "$PREREQUISITES_ROOT/client/node_modules/.bin/jest" ]; then
    return 0
  fi
  echo "pre-push: client/node_modules missing or incomplete (cd client && npm ci)." >&2
  _prerequisites_note_bootstrap
  return 1
}

# Sets RUN (a python that has pytest, up to `-m`) and RUFF_CMD.
resolve_server_runner() {
  local venv="$PREREQUISITES_ROOT/server/.venv"
  if [ -x "$venv/bin/python" ] && "$venv/bin/python" -c "import pytest" 2>/dev/null; then
    RUN=("$venv/bin/python" "-m")
    RUFF_CMD=("$venv/bin/ruff")
  elif command -v uv >/dev/null 2>&1; then
    RUN=(uv run --extra dev python -m)
    RUFF_CMD=(uv run --extra dev ruff)
  else
    echo "pre-push: need server/.venv (with pytest) or uv on PATH." >&2
    echo "Run: cd server && uv sync --extra dev" >&2
    _prerequisites_note_bootstrap
    return 1
  fi
}
