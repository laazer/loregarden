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

# Sourced beside it: the selector flags for this run. A push runs the tests
# nearest its change, since its PR gets CI's full run. LOREGARDEN_TESTS_BASE is
# how a landing names its range, and nothing runs after a landing to catch what
# a nearer selection misses, so a named range gets the whole reach.
SELECT_REACH=()
if [ -z "${LOREGARDEN_TESTS_BASE:-}" ]; then
  SELECT_REACH=(--nearest)
fi
