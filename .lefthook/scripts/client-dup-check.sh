#!/usr/bin/env bash
# Pre-commit: fail if client/src has duplicated blocks (jscpd).
# Lefthook glob already limits invocation to staged client TS/TSX.
# Config: jscpd.json at repo root.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLIENT_ROOT="$ROOT/client"

# shellcheck source=ensure-node.sh
source "$SCRIPT_DIR/ensure-node.sh"

if [ ! -x "$CLIENT_ROOT/node_modules/.bin/jscpd" ]; then
  echo "pre-commit: jscpd not found (cd client && npm ci)." >&2
  exit 1
fi

echo "pre-commit: running jscpd on client/src ..."
cd "$ROOT"
"$CLIENT_ROOT/node_modules/.bin/jscpd" --config jscpd.json
