#!/usr/bin/env bash
# Pre-push: run the server suite, unless every test it would run has already
# passed on exactly this tree (pass_receipt.py). Then only the lint steps run,
# and nothing queues for capacity.
#
#   push-server-tests.sh <command that queues and runs the suite…>
#
# LOREGARDEN_FULL_TESTS=1 always runs the suite. A push only: a landing calls
# server-tests.sh itself and never reads a receipt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hook-noninteractive.sh
source "$SCRIPT_DIR/hook-noninteractive.sh"
# shellcheck source=push-base.sh
source "$SCRIPT_DIR/push-base.sh"

ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ $# -eq 0 ]; then
  echo "push-server-tests: usage: push-server-tests.sh <command…>" >&2
  exit 2
fi

# The files this push would run: the selection (budgeted, see push-base.sh),
# or every test file if the selector still says the change cannot be narrowed. Nothing on any other
# failure, which server-tests.sh then meets and reports.
wanted_tests() {
  local base targets status=0
  base="$(select_base "$ROOT")"
  if [ -n "$base" ]; then
    targets="$(bash "$SCRIPT_DIR/server_python.sh" "$SCRIPT_DIR/select_pytest_targets.py" \
      --repo "$ROOT" --base "$base" ${SELECT_REACH[@]+"${SELECT_REACH[@]}"} 2>/dev/null)" || status=$?
  else
    status=2
  fi
  case "$status" in
    0) printf '%s\n' "$targets" ;;
    2) git -C "$ROOT" ls-files 'server/tests/test_*.py' 'server/tests/**/test_*.py' ;;
  esac
}

if [ -z "${LOREGARDEN_FULL_TESTS:-}" ]; then
  WANTED="$(wanted_tests)"
  if PASSED="$(bash "$SCRIPT_DIR/server_python.sh" "$SCRIPT_DIR/pass_receipt.py" covers \
    --repo "$ROOT" <<< "$WANTED")"; then
    echo "pre-push: $PASSED; not running them again (LOREGARDEN_FULL_TESTS=1 does)"
    exec bash "$SCRIPT_DIR/server-tests.sh" --lint-only
  fi
fi

exec "$@"
