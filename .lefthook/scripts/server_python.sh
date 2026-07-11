#!/usr/bin/env bash
# Run Python using the server/ project environment (same interpreter/deps as
# `uv sync --extra dev` in CI). Does not change cwd, so repo-root-relative
# arguments (e.g. staged file paths from lefthook) resolve correctly either way.
#
# Resolution order:
#   1) server/.venv/bin/python if present and executable
#   2) uv run --project server --extra dev python if uv is on PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PY_ROOT="$REPO_ROOT/server"

if [[ ! -d "$PY_ROOT" ]]; then
  echo "server_python: missing $PY_ROOT" >&2
  exit 127
fi

if [[ -x "$PY_ROOT/.venv/bin/python" ]]; then
  exec "$PY_ROOT/.venv/bin/python" "$@"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run --project "$PY_ROOT" --extra dev python "$@"
fi

echo "server_python: no venv at $PY_ROOT/.venv and uv not on PATH." >&2
echo "Create the env: cd $PY_ROOT && uv sync --extra dev" >&2
exit 127
