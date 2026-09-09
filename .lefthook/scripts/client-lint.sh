#!/usr/bin/env bash
# Pre-commit: oxlint on staged client/ files (fast, no type info required).
set -euo pipefail

if [ "$#" -eq 0 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLIENT_ROOT="$ROOT/client"

# shellcheck source=ensure-node.sh
source "$SCRIPT_DIR/ensure-node.sh"

if [ ! -x "$CLIENT_ROOT/node_modules/.bin/oxlint" ]; then
  echo "pre-commit: oxlint not found (cd client && npm ci)." >&2
  exit 1
fi

rel_args=()
for f in "$@"; do
  case "$f" in
    client/*)
      rel_args+=("${f#client/}")
      ;;
  esac
done

if [ "${#rel_args[@]}" -eq 0 ]; then
  exit 0
fi

echo "pre-commit: running oxlint on staged client files..."
cd "$CLIENT_ROOT"
# Deliberately not --deny-warnings: the repo carries unrelated warning debt
# (exhaustive-deps, only-export-components) that would block every commit. The
# swallowed-error cases oxlint's warnings hint at are enforced properly, with
# waivers, by ts_no_silent_failures_check.cjs.
./node_modules/.bin/oxlint "${rel_args[@]}"
