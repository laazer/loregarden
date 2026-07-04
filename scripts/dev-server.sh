#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/server"
export LOREGARDEN_REPO_ROOT="$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  uv venv
fi

uv sync

exec uv run uvicorn loregarden.main:app --reload --host 127.0.0.1 --port 8000
