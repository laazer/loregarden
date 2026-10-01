#!/usr/bin/env bash
# Pre-commit wrapper: theme-token checks on staged client files (.ts, .tsx, .css).
set -euo pipefail

if [ "$#" -eq 0 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLIENT_ROOT="$ROOT/client"

# shellcheck source=ensure-node.sh
source "$SCRIPT_DIR/ensure-node.sh"

for dep in @typescript-eslint/typescript-estree postcss; do
  if [ ! -d "$CLIENT_ROOT/node_modules/$dep" ]; then
    echo "pre-commit: $dep missing (cd client && npm ci)." >&2
    exit 1
  fi
done

cd "$ROOT"
exec node "$SCRIPT_DIR/ts_theme_check.cjs" "$@"
