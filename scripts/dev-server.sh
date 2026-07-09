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
#
# Claude usage modal (and Baxter, which shells out to `claude` directly) show
# "not logged in" even when `claude` is logged in interactively? macOS Keychain
# requires GUI interaction to release a credential's value to a background
# process, so this server — and even a backgrounded `claude` subprocess itself —
# gets silently denied, no prompt. See https://code.claude.com/docs/en/authentication.
# One-time fix: `task claude:setup-token` (saves the printed token to
# data/.claude-oauth-token, chmod 600 — don't pipe `claude setup-token` straight
# to a file yourself, its interactive UI gets captured too, not just the token).
# Exporting it below as a real env var means both this server's own usage-API
# calls AND every `claude` subprocess it spawns (Baxter, CLI adapters) pick it
# up automatically — it's item #5 in Claude Code's own auth precedence order.
# Note: this token is scoped to inference only, so it fixes Baxter but the
# Usage modal's live rate-limit numbers may still show HTTP 403 — see
# usage_service.py's _format_usage_http_error for why.
CLAUDE_OAUTH_TOKEN_FILE="$ROOT/data/.claude-oauth-token"
if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -s "$CLAUDE_OAUTH_TOKEN_FILE" ]]; then
  export CLAUDE_CODE_OAUTH_TOKEN="$(<"$CLAUDE_OAUTH_TOKEN_FILE")"
fi
#
# iCloud + Obsidian memory (optional):
#   LOREGARDEN_DATABASE_URL=sqlite:///$HOME/Library/Mobile Documents/com~apple~CloudDocs/Loregarden/loregarden.db
#   LOREGARDEN_OBSIDIAN_VAULT_DIR=$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/MyVault
#   LOREGARDEN_MEMORY_SQLITE_URL=sqlite:///$HOME/Library/Mobile Documents/com~apple~CloudDocs/Loregarden/memory.db
#   LOREGARDEN_ICLOUD_ROOT=   — override auto-detected iCloud Drive root

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  uv venv
fi

uv sync

RELOAD_TRIGGER="$ROOT/server/.self-improve-restart"
touch "$RELOAD_TRIGGER"

exec uv run uvicorn loregarden.main:app --reload --host 127.0.0.1 --port 8000 \
  --reload-include '.self-improve-restart'
