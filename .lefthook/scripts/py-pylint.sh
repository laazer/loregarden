#!/usr/bin/env bash
# Run Pylint (too-many-statements only) on staged Python under server/ using
# server/pyproject.toml [tool.pylint.*].
set -euo pipefail

if [ "$#" -eq 0 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PY_ROOT="$ROOT/server"
# shellcheck source=py-staged-paths.sh
source "$SCRIPT_DIR/py-staged-paths.sh"
# Keep Pylint stats/cache inside the project (avoids $HOME/Caches and sandbox issues in CI).
export PYLINTHOME="${PYLINTHOME:-$PY_ROOT/.pylint_home}"

if [ ! -f "$PY_ROOT/pyproject.toml" ]; then
  echo "pre-commit: missing Pylint config at $PY_ROOT/pyproject.toml" >&2
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

if [ -x ".venv/bin/python" ] && ".venv/bin/python" -c "import pylint" 2>/dev/null; then
  PY_CMD=(".venv/bin/python")
elif command -v uv >/dev/null 2>&1; then
  PY_CMD=(uv run --extra dev python)
else
  echo "pre-commit: pylint is required (cd server && uv sync --extra dev)." >&2
  exit 1
fi

echo "pre-commit: running Pylint (too-many-statements, diff-scoped) on staged files..."
"${PY_CMD[@]}" "$SCRIPT_DIR/pylint_diff_filter.py" "${rel_args[@]}"
