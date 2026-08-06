#!/usr/bin/env bash
# Pre-commit: auto-format staged files (lefthook stage_fixed restages).
#   - Python under server/: ruff format + ruff check --fix
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

py_files=()
client_code=()

for f in "$@"; do
  if [[ "$f" == server/* ]] && [[ "$f" == *.py ]]; then
    py_files+=("$f")
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
