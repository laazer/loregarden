#!/usr/bin/env bash
# The `loregarden` CLI, run from this checkout without installing it globally.
#   ./scripts/loregarden-cli.sh mcp list
#   ./scripts/loregarden-cli.sh mcp call loregarden_get_ticket ticket_id=42
#   ./scripts/loregarden-cli.sh db init --empty
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The code always comes from THIS checkout — the script's own path. The database
# comes from the primary worktree, because there is only one of it and a linked
# worktree's `data/loregarden.db` is an empty file nobody meant to read.
#
# Keeping those two apart is the point. When one variable chose both, the only way
# to reach the live database was to `cd` into the main checkout, which silently ran
# whatever branch that checkout happened to be on — and a build that predates a
# migration writes rows the current code cannot spell.
resolve_primary_checkout() {
  local common
  common="$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null)" || return 1
  case "$common" in
    /*) ;;
    *) common="$ROOT/$common" ;;
  esac
  local primary
  primary="$(cd "$common/.." 2>/dev/null && pwd)" || return 1
  # Only trust it if it looks like this project and actually holds the database.
  [ -d "$primary/agent_context" ] && [ -d "$primary/server" ] || return 1
  [ -f "$primary/data/loregarden.db" ] || return 1
  printf '%s' "$primary"
}

if [ -z "${LOREGARDEN_REPO_ROOT:-}" ]; then
  if primary="$(resolve_primary_checkout)"; then
    LOREGARDEN_REPO_ROOT="$primary"
  else
    LOREGARDEN_REPO_ROOT="$ROOT"
  fi
fi
export LOREGARDEN_REPO_ROOT

cd "$ROOT/server"
exec uv run loregarden "$@"
