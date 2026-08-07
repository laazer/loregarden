#!/usr/bin/env bash
# The `loregarden` CLI, run from this checkout without installing it globally.
#   ./scripts/loregarden-cli.sh mcp list
#   ./scripts/loregarden-cli.sh mcp call loregarden_get_ticket ticket_id=42
#   ./scripts/loregarden-cli.sh db init --empty
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/server"
# Honour a preset root so a worktree can be pointed at the main checkout's database.
export LOREGARDEN_REPO_ROOT="${LOREGARDEN_REPO_ROOT:-$ROOT}"
exec uv run loregarden "$@"
