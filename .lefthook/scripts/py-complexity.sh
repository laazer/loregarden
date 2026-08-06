#!/usr/bin/env bash
# Diff-scoped Ruff McCabe complexity (C901) on staged Python under server/.
# Does not add C901 to the main ruff select — CI `ruff check .` stays free of
# pre-existing debt; this hook only blocks complexity *growth* on touched funcs.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PY_ROOT="$ROOT/server"
# shellcheck source=py-staged-paths.sh
source "$SCRIPT_DIR/py-staged-paths.sh"

if [ ! -f "$PY_ROOT/pyproject.toml" ]; then
  echo "pre-commit: missing Ruff config at $PY_ROOT/pyproject.toml" >&2
  exit 1
fi

rel_args=()
for f in "$@"; do
  mapped="$(py_staged_server_rel "$f" "$PY_ROOT" || true)"
  if [ -n "${mapped:-}" ]; then
    rel_args+=("$mapped")
  fi
done

if [ "${#rel_args[@]}" -eq 0 ]; then
  exit 0
fi

cd "$PY_ROOT"

if [ -x ".venv/bin/python" ] && ".venv/bin/python" -c "import ruff" 2>/dev/null; then
  PY_CMD=(".venv/bin/python")
elif [ -x ".venv/bin/python" ]; then
  # ruff is typically installed as a binary, not an importable module
  PY_CMD=(".venv/bin/python")
elif command -v uv >/dev/null 2>&1; then
  PY_CMD=(uv run --extra dev python)
else
  echo "pre-commit: python+ruff required (cd server && uv sync --extra dev)." >&2
  exit 1
fi

echo "pre-commit: running Ruff C901 (McCabe complexity, diff-scoped) on staged files..."
"${PY_CMD[@]}" "$SCRIPT_DIR/ruff_complexity_diff_filter.py" --repo-prefix server "${rel_args[@]}"
