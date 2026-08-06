#!/usr/bin/env bash
# Pre-commit: mypy on staged server Python files.
# Skips tests and migration modules (isolation / generated noise).
set -euo pipefail

# shellcheck source=hook-noninteractive.sh
source "$(cd "$(dirname "$0")" && pwd)/hook-noninteractive.sh"

if [ "$#" -eq 0 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVER_ROOT="$ROOT/server"
VENV_PY="$SERVER_ROOT/.venv/bin/python"

files=()
for f in "$@"; do
  [[ "$f" == server/* ]] || continue
  [[ "$f" == *.py ]] || continue
  case "$f" in
    server/tests/*|*/migrations.py|*/migrations_*.py)
      continue
      ;;
  esac
  files+=("${f#server/}")
done

if [ "${#files[@]}" -eq 0 ]; then
  exit 0
fi

if [ ! -x "$VENV_PY" ]; then
  echo "pre-commit: server/.venv missing; run: cd server && uv sync" >&2
  exit 1
fi

if ! "$VENV_PY" -c 'import mypy' 2>/dev/null; then
  echo "pre-commit: mypy not installed; run: cd server && uv sync" >&2
  exit 1
fi

echo "pre-commit: mypy on ${#files[@]} staged file(s) ..."
cd "$SERVER_ROOT"
exec "$VENV_PY" -m mypy --config-file pyproject.toml "${files[@]}"
