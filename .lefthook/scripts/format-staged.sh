#!/usr/bin/env bash
# Pre-commit: auto-format staged files (lefthook stage_fixed restages).
#   - Python under server/: ruff format + ruff check --fix
#   - Python scripts outside server/ (PY_SCRIPT_DIRS): the same, at the
#     system python3's target version (see py-staged-paths.sh)
#   - Client TS/TSX/JS/JSX: oxlint --fix
# No prettier yet — would mass-rewrite the client without an agreed style guide.
set -euo pipefail

# shellcheck source=hook-noninteractive.sh
source "$(cd "$(dirname "$0")" && pwd)/hook-noninteractive.sh"

if [ "$#" -eq 0 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVER_ROOT="$ROOT/server"
CLIENT_ROOT="$ROOT/client"
RUFF="$SERVER_ROOT/.venv/bin/ruff"

# shellcheck source=ensure-node.sh
source "$SCRIPT_DIR/ensure-node.sh"
# shellcheck source=py-staged-paths.sh
source "$SCRIPT_DIR/py-staged-paths.sh"

py_files=()
py_scripts=()
client_code=()

for f in "$@"; do
  if [[ "$f" == server/* ]] && [[ "$f" == *.py ]]; then
    py_files+=("$f")
  elif py_is_script_path "$f"; then
    # Run from server/ like py-review, so both read the config the same way.
    py_scripts+=("../$f")
  fi
  if [[ "$f" == client/* ]]; then
    case "$f" in
      *.ts|*.tsx|*.js|*.jsx)
        client_code+=("$f")
        ;;
    esac
  fi
done

if [ "${#py_files[@]}" -gt 0 ]; then
  if [ ! -x "$RUFF" ]; then
    echo "pre-commit: ruff missing (cd server && uv sync)." >&2
    exit 1
  fi
  echo "pre-commit: ruff format/fix on ${#py_files[@]} Python file(s) ..."
  "$RUFF" format "${py_files[@]}"
  # Auto-fixable lint only; remaining issues fail in py-review.
  "$RUFF" check --fix --quiet "${py_files[@]}" || true
fi

if [ "${#py_scripts[@]}" -gt 0 ]; then
  if [ ! -x "$RUFF" ]; then
    echo "pre-commit: ruff missing (cd server && uv sync)." >&2
    exit 1
  fi
  echo "pre-commit: ruff format/fix on ${#py_scripts[@]} Python script(s) outside server/ ..."
  (
    cd "$SERVER_ROOT"
    "$RUFF" format --config pyproject.toml --target-version "$PY_SCRIPT_TARGET" "${py_scripts[@]}"
    # Auto-fixable lint only; remaining issues fail in py-review.
    "$RUFF" check --fix --quiet --config pyproject.toml --target-version "$PY_SCRIPT_TARGET" \
      "${py_scripts[@]}" || true
  )
fi

if [ "${#client_code[@]}" -gt 0 ]; then
  OXLINT="$CLIENT_ROOT/node_modules/.bin/oxlint"
  if [ ! -x "$OXLINT" ]; then
    echo "pre-commit: oxlint missing (cd client && npm ci)." >&2
    exit 1
  fi
  echo "pre-commit: oxlint --fix on ${#client_code[@]} file(s) ..."
  rel=()
  for f in "${client_code[@]}"; do
    rel+=("${f#client/}")
  done
  (cd "$CLIENT_ROOT" && ./node_modules/.bin/oxlint --fix "${rel[@]}") || true
fi

exit 0
