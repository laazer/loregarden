#!/usr/bin/env bash
# Pre-commit: mypy on staged server Python files, and on the stdlib-only
# scripts outside server/ (PY_SCRIPT_DIRS in py-staged-paths.sh).
# Skips tests and migration modules (isolation / generated noise).
#
# mypy reads the scripts at server/pyproject.toml's python_version (3.11); it
# refuses to target 3.9, so 3.9 compatibility is ruff's job (py-review), not
# this gate's.
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
# shellcheck source=py-staged-paths.sh
source "$SCRIPT_DIR/py-staged-paths.sh"

files=()
for f in "$@"; do
  if py_is_script_path "$f"; then
    files+=("../$f")
    continue
  fi
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
