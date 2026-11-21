#!/usr/bin/env bash
# SessionStart: install a fresh worktree's dependencies before they are needed.
#
# A new worktree has no server/.venv and no client/node_modules, and nothing
# says so until a gate or a push needs them: pre-commit's ruff/mypy refuse,
# and pre-push dies with a bare "failed to push" after its queue wait. Every
# session in a new worktree paid one detour for each.
#
# When either is missing, this starts `uv sync --extra dev` and/or `npm ci` in
# the background (the session does not wait), logs to the git dir, and tells
# the session where. A lock stops two sessions in one worktree installing at
# once. The primary checkout is left alone: its dependencies are the
# operator's, and a reinstall under a running server would break it.
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$ROOT" 2>/dev/null || exit 0

# A linked worktree's `.git` is a file ("gitdir: …"); the primary's is a directory.
[ -f .git ] || exit 0
gitdir="$(sed -n 's/^gitdir: //p' .git)"
[ -n "$gitdir" ] && [ -d "$gitdir" ] || exit 0

need_server=0
need_client=0
[ -x server/.venv/bin/python ] || need_server=1
[ -x client/node_modules/.bin/jest ] || need_client=1
[ "$need_server" -eq 1 ] || [ "$need_client" -eq 1 ] || exit 0

log="$gitdir/worktree-bootstrap.log"
lock="$gitdir/worktree-bootstrap.lock"

if ! mkdir "$lock" 2>/dev/null; then
  echo "Worktree setup is already running (another session started it); log: $log. Wait for it before running gates, tests or a push."
  exit 0
fi

missing=""
[ "$need_server" -eq 1 ] && missing="server/.venv"
[ "$need_client" -eq 1 ] && missing="${missing:+$missing and }client/node_modules"

(
  trap 'rmdir "$lock" 2>/dev/null' EXIT
  status=0
  echo "worktree-bootstrap: started $(date -u +%FT%TZ) for $missing"
  if [ "$need_server" -eq 1 ]; then
    if command -v uv >/dev/null 2>&1; then
      (cd server && uv sync --extra dev) || status=1
    else
      echo "worktree-bootstrap: uv not on PATH; run: cd server && uv sync --extra dev" >&2
      status=1
    fi
  fi
  if [ "$need_client" -eq 1 ]; then
    # shellcheck source=../../.lefthook/scripts/ensure-node.sh
    (source .lefthook/scripts/ensure-node.sh && cd client && npm ci --no-audit --no-fund) || status=1
  fi
  if [ "$status" -eq 0 ]; then
    echo "worktree-bootstrap: done $(date -u +%FT%TZ)"
  else
    echo "worktree-bootstrap: FAILED $(date -u +%FT%TZ); see the errors above" >&2
  fi
) >"$log" 2>&1 </dev/null &
disown 2>/dev/null || true

echo "This worktree had no $missing; installing it in the background (log: $log). It is done when the log's last line says 'done' (or FAILED). Gates, tests and pushes need it first."
exit 0
