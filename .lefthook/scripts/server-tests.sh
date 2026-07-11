#!/usr/bin/env bash
# Pre-push: mirror the "Server (Python)" CI job exactly
# (.github/workflows/ci.yml) — ruff lint, ruff format check, pytest.
set -euo pipefail

# shellcheck source=hook-noninteractive.sh
source "$(cd "$(dirname "$0")" && pwd)/hook-noninteractive.sh"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY_ROOT="$ROOT/server"

cd "$PY_ROOT"

if [ -x ".venv/bin/python" ] && ".venv/bin/python" -c "import pytest" 2>/dev/null; then
  RUN=(".venv/bin/python" "-m")
  RUFF_CMD=(".venv/bin/ruff")
elif command -v uv >/dev/null 2>&1; then
  RUN=(uv run --extra dev python -m)
  RUFF_CMD=(uv run --extra dev ruff)
else
  echo "pre-push: need server/.venv (with pytest) or uv on PATH." >&2
  echo "Run: cd server && uv sync --extra dev" >&2
  exit 1
fi

echo "pre-push: ruff check ..."
"${RUFF_CMD[@]}" check .

echo "pre-push: ruff format --check ..."
"${RUFF_CMD[@]}" format --check .

echo "pre-push: pytest -q ..."
LOREGARDEN_REPO_ROOT="$ROOT" "${RUN[@]}" pytest -q
