#!/usr/bin/env bash
# Run Ruff check (no autofix) on staged Python using server/pyproject.toml.
# Check-only so pre-commit behavior matches CI's `ruff check .` exactly
# (.github/workflows/ci.yml) instead of silently mutating staged files.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  exit 0
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY_ROOT="$ROOT/server"
# shellcheck source=py-staged-paths.sh
source "$(dirname "$0")/py-staged-paths.sh"

if [ ! -f "$PY_ROOT/pyproject.toml" ]; then
  echo "pre-commit: missing Ruff config at $PY_ROOT/pyproject.toml" >&2
  exit 1
fi

server_args=()
script_args=()
for f in "$@"; do
  mapped="$(py_staged_server_rel "$f" "$PY_ROOT" || true)"
  case "${mapped:-}" in
    "") ;;
    ../*) script_args+=("$mapped") ;;
    *) server_args+=("$mapped") ;;
  esac
done

if [ "${#server_args[@]}" -eq 0 ] && [ "${#script_args[@]}" -eq 0 ]; then
  exit 0
fi

if [ -x "$PY_ROOT/.venv/bin/ruff" ]; then
  RUFF_CMD=("$PY_ROOT/.venv/bin/ruff")
elif command -v uv >/dev/null 2>&1; then
  RUFF_CMD=(uv)
elif command -v ruff >/dev/null 2>&1; then
  RUFF_CMD=(ruff)
else
  echo "pre-commit: ruff is required (cd server && uv sync --extra dev)." >&2
  exit 1
fi

ruff_check() {
  if [ "${RUFF_CMD[0]}" = "uv" ]; then
    uv run --extra dev ruff check --config pyproject.toml "$@"
  else
    "${RUFF_CMD[@]}" check --config pyproject.toml "$@"
  fi
}

echo "pre-commit: running Ruff (server/pyproject.toml) on staged files..."
cd "$PY_ROOT"
status=0
if [ "${#server_args[@]}" -gt 0 ]; then
  ruff_check "${server_args[@]}" || status=$?
fi
# The scripts outside server/ run under the system python3; see py-staged-paths.sh.
if [ "${#script_args[@]}" -gt 0 ]; then
  ruff_check --target-version "$PY_SCRIPT_TARGET" "${script_args[@]}" || status=$?
fi
exit "$status"
