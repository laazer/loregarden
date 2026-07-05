#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/server"
export LOREGARDEN_REPO_ROOT="$ROOT"

# CLI adapters — Loregarden routes permission prompts to the approval inbox by default.
#   LOREGARDEN_CLI_ADAPTER=local|claude|cursor|lmstudio
#   LOREGARDEN_ALLOW_PERMISSION_BYPASS=1   — dev-only escape hatch (NOT for normal use)
#   LOREGARDEN_CLAUDE_BIN=claude
#   LOREGARDEN_CURSOR_BIN=cursor-agent
#   LOREGARDEN_LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1
#   LOREGARDEN_LMSTUDIO_MODEL=   — optional; uses first loaded model when empty
#   LOREGARDEN_LMSTUDIO_STREAM=1 — stream tokens to run logs
#   LOREGARDEN_MCP_URL=http://127.0.0.1:8000/mcp

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  uv venv
fi

uv sync

exec uv run uvicorn loregarden.main:app --reload --host 127.0.0.1 --port 8000
