#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/server"
export LOREGARDEN_REPO_ROOT="$ROOT"
export LOREGARDEN_API_BASE="${LOREGARDEN_API_BASE:-http://127.0.0.1:8000}"
# Prefer the embedded endpoint: POST $LOREGARDEN_API_BASE/mcp (same server as the API).
# This script is a stdio proxy for MCP clients that only support command-based servers.
if [[ "${LOREGARDEN_MCP_INPROCESS:-}" != "1" ]]; then
  uv sync
fi
exec uv run python -m loregarden.cli.mcp_server "$@"
