#!/usr/bin/env bash
# Pre-push: refuse a push whose suite cannot start, before it queues for capacity.
#
#   push-prerequisites.sh server-tests | client-tests
#
# Holds no lease and runs no test. Asks only for what this push's suite will
# use: the server suite needs client/node_modules when the tests it selects run
# the TypeScript gates, and not otherwise.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hook-noninteractive.sh
source "$SCRIPT_DIR/hook-noninteractive.sh"
# shellcheck source=test-prerequisites.sh
source "$SCRIPT_DIR/test-prerequisites.sh"
# shellcheck source=push-base.sh
source "$SCRIPT_DIR/push-base.sh"

ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Whether the tests this push runs include one that needs client/node_modules.
# Those say so through server/tests/toolchain.py. Asks the selector server-tests.sh
# asks; whenever that run would be the full suite, the answer is yes.
server_tests_need_client() {
  local base targets
  base="$(push_base "$ROOT")"
  if [ -n "${LOREGARDEN_FULL_TESTS:-}" ] || [ -z "$base" ]; then
    return 0
  fi
  # Its reason for refusing to narrow is server-tests.sh's to print.
  if ! targets="$(bash "$SCRIPT_DIR/server_python.sh" "$SCRIPT_DIR/select_pytest_targets.py" \
    --repo "$ROOT" --base "$base" 2>/dev/null)"; then
    return 0
  fi
  [ -n "$targets" ] || return 0
  local files=()
  while IFS= read -r target; do
    files+=("$ROOT/$target")
  done <<< "$targets"
  # A file grep cannot read (exit 2) must not read as "needs nothing".
  local status=0
  grep -lqE 'require_client_modules|NPM_CI' "${files[@]}" || status=$?
  [ "$status" -ne 1 ]
}

require_client_toolchain() {
  # shellcheck source=ensure-node.sh
  source "$SCRIPT_DIR/ensure-node.sh"
  require_client_modules
}

case "${1:-}" in
  server-tests)
    resolve_server_runner
    if [ -x "$ROOT/client/node_modules/.bin/jest" ]; then
      exit 0 # installed: no need to spend the selection finding out
    fi
    if server_tests_need_client; then
      require_client_toolchain
    fi
    ;;
  client-tests)
    require_client_toolchain
    ;;
  *)
    echo "push-prerequisites: usage: push-prerequisites.sh server-tests | client-tests" >&2
    exit 2
    ;;
esac
