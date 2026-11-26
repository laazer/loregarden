#!/usr/bin/env bash
# Sourced: the base for "what is being pushed" — the remote-tracking commit when
# the branch already exists there, otherwise everything this branch adds to main.
# LOREGARDEN_TESTS_BASE overrides it. Prints nothing when there is no base.
push_base() {
  local root="$1" base="${LOREGARDEN_TESTS_BASE:-}"
  if [ -z "$base" ]; then
    base="$(git -C "$root" rev-parse --verify --quiet "@{push}" || true)"
  fi
  if [ -z "$base" ]; then
    base="$(git -C "$root" rev-parse --verify --quiet origin/main || true)"
  fi
  printf '%s' "$base"
}

# Sourced beside it: the selector flags for this run. A push runs at most
# LOREGARDEN_PUSH_TEST_FILES (default 40) of the test files nearest its change
# and never the full suite: CI and the merge queue run that before anything
# lands. On 2026-11-25 pre-push full suites held this machine's capacity for 1-4
# hours each while CI ran the same suite in about 9 minutes. 40 files is about
# 2.5 minutes at 3 workers under load (~0.75s a test serial, ~14 tests a file).
# LOREGARDEN_TESTS_BASE is how a landing names its range, and nothing runs after
# a landing to catch what a nearer selection misses, so a named range gets the
# whole reach and no budget.
SELECT_REACH=()
if [ -z "${LOREGARDEN_TESTS_BASE:-}" ]; then
  PUSH_TEST_FILES="${LOREGARDEN_PUSH_TEST_FILES:-40}"
  if ! [[ "$PUSH_TEST_FILES" =~ ^[1-9][0-9]*$ ]]; then
    echo "push-base: LOREGARDEN_PUSH_TEST_FILES must be a positive integer, got '$PUSH_TEST_FILES'" >&2
    exit 2
  fi
  SELECT_REACH=(--nearest --budget "$PUSH_TEST_FILES")
fi

# The base the server selector diffs against. A push with nothing to diff
# against diffs HEAD with itself, which the budgeted selector answers with an
# even sample rather than the full suite. Empty for a landing without a base.
select_base() {
  local base
  base="$(push_base "$1")"
  if [ -z "$base" ] && [ ${#SELECT_REACH[@]} -gt 0 ]; then
    base=HEAD
  fi
  printf '%s' "$base"
}
