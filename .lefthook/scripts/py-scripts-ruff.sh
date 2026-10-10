#!/usr/bin/env bash
# Ruff over every Python script outside server/ (PY_SCRIPT_DIRS in
# py-staged-paths.sh): the whole-tree counterpart of py-review and
# format-staged, for CI and pre-push, whose `ruff check .` runs from server/
# and never reaches them.
#
# Usage: py-scripts-ruff.sh check
#        py-scripts-ruff.sh format --check
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PY_ROOT="$ROOT/server"
# shellcheck source=py-staged-paths.sh
source "$SCRIPT_DIR/py-staged-paths.sh"

if [ "$#" -eq 0 ]; then
  echo "usage: $0 (check | format --check) [ruff flags...]" >&2
  exit 2
fi

if [ -x "$PY_ROOT/.venv/bin/ruff" ]; then
  RUFF_CMD=("$PY_ROOT/.venv/bin/ruff")
elif command -v uv >/dev/null 2>&1; then
  RUFF_CMD=(uv run --project "$PY_ROOT" --extra dev ruff)
else
  echo "py-scripts-ruff: ruff is required (cd server && uv sync --extra dev)." >&2
  exit 1
fi

# A directory may not exist on every branch (.claude/hooks is new); a missing
# one has nothing to lint. The list printed below says what was covered.
dirs=()
for dir in "${PY_SCRIPT_DIRS[@]}"; do
  [ -d "$ROOT/$dir" ] && dirs+=("../$dir")
done
if [ "${#dirs[@]}" -eq 0 ]; then
  echo "py-scripts-ruff: none of ${PY_SCRIPT_DIRS[*]} exist; nothing to lint."
  exit 0
fi

subcommand="$1"
shift
echo "py-scripts-ruff: ruff $subcommand $* (target $PY_SCRIPT_TARGET) on ${dirs[*]#../}"
# From server/, like py-review, so the config resolves the same way.
cd "$PY_ROOT"
"${RUFF_CMD[@]}" "$subcommand" --config pyproject.toml --target-version "$PY_SCRIPT_TARGET" "$@" "${dirs[@]}"
