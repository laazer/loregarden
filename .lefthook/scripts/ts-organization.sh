#!/usr/bin/env bash
# Pre-commit wrapper: TypeScript organization checks on staged client files.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLIENT_ROOT="$ROOT/client"

# shellcheck source=ensure-node.sh
source "$SCRIPT_DIR/ensure-node.sh"

if [ ! -d "$CLIENT_ROOT/node_modules/@typescript-eslint/typescript-estree" ]; then
  echo "pre-commit: @typescript-eslint/typescript-estree missing (cd client && npm ci)." >&2
  exit 1
fi

cd "$ROOT"
exec node "$SCRIPT_DIR/ts_organization_check.cjs" "$@"
