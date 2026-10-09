#!/usr/bin/env bash
# Pre-commit wrapper: motion checks on staged client stylesheets.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=ensure-node.sh
source "$SCRIPT_DIR/ensure-node.sh"

# A missing client toolchain is reported by the checker itself, as exit 69
# ("could not run") — see gate_client_modules.cjs.

cd "$ROOT"
exec node "$SCRIPT_DIR/ts_motion_check.cjs" "$@"
