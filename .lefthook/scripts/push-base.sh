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
# LOREGARDEN_TESTS_BASE is how a landing names its range. A landing runs at most
# LOREGARDEN_LAND_TEST_FILES (default 300) of the nearest test files, and never
# the full suite. Every landing opens or updates its tree's PR, and the merge
# queue runs CI's full suite on that PR merged onto main, so what the cap leaves
# out is caught before main. Uncapped, about half of landings selected ~640
# files (a change to enums.py or tools.py is within two imports of nearly every
# test) and ran into the 30-minute wall limit, which refuses the land. 20 land
# runs to 2026-11-27 took 2.1s a file at the median and 2.9s at worst, so 300
# files is about 15 minutes at worst: half the limit, beside jest and lint.
# A full-suite fallback could not finish inside the limit either.
if [ -n "${LOREGARDEN_TESTS_BASE:-}" ]; then
  BUDGET_VAR=LOREGARDEN_LAND_TEST_FILES
  BUDGET_FILES="${LOREGARDEN_LAND_TEST_FILES:-300}"
else
  BUDGET_VAR=LOREGARDEN_PUSH_TEST_FILES
  BUDGET_FILES="${LOREGARDEN_PUSH_TEST_FILES:-40}"
fi
if ! [[ "$BUDGET_FILES" =~ ^[1-9][0-9]*$ ]]; then
  echo "push-base: $BUDGET_VAR must be a positive integer, got '$BUDGET_FILES'" >&2
  exit 2
fi
SELECT_REACH=(--nearest --budget "$BUDGET_FILES")

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
