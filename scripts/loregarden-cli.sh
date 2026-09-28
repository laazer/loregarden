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
# shellcheck source=lib/primary-checkout.sh
source "$ROOT/scripts/lib/primary-checkout.sh"

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
