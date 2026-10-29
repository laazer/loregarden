#!/usr/bin/env bash
# Pre-push: the "Client (TypeScript)" CI job (.github/workflows/ci.yml) — lint,
# type check, test — with jest narrowed to the tests the pushed commits reach.
#
# oxlint and `tsc -b` still cover everything: both are seconds, and tsc is
# incremental anyway. jest runs with --changedSince, which selects through its
# own module graph, and falls back to the full run whenever a change lands on
# something every test depends on (jest config, package manifest, tsconfig).
#
# CI still runs the whole suite on every PR. LOREGARDEN_FULL_TESTS=1 forces the
# full run here; LOREGARDEN_TESTS_BASE=<ref> overrides the base.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hook-noninteractive.sh
source "$SCRIPT_DIR/hook-noninteractive.sh"
# shellcheck source=ensure-node.sh
source "$SCRIPT_DIR/ensure-node.sh"
# shellcheck source=test-workers.sh
source "$SCRIPT_DIR/test-workers.sh"
# shellcheck source=push-progress.sh
source "$SCRIPT_DIR/push-progress.sh"
# shellcheck source=test-count-guard.sh
source "$SCRIPT_DIR/test-count-guard.sh"

ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLIENT_ROOT="$ROOT/client"

cd "$CLIENT_ROOT"

if [ ! -d node_modules ]; then
  echo "pre-push: client/node_modules missing (cd client && npm ci)." >&2
  exit 1
fi

# A private temp root for this run, holding the one file jest's own reporter
# records its suite count in. Removed on exit, never the progress file, and
# written only by the reporter: run_and_judge reads it after jest exits, so a
# selection that ran nothing cannot leave this push looking green.
COUNT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/loregarden-jest-XXXXXX")"
trap 'rc=$?; progress_finish "$rc"; rm -rf "$COUNT_ROOT" 2>/dev/null || true' EXIT
export LOREGARDEN_TEST_COUNT_FILE="$COUNT_ROOT/test-count.json"
progress_begin "client-tests" 3

progress_step "oxlint"
npm run lint

progress_step "tsc -b"
npx tsc -b

# Base for "what is being pushed": the remote-tracking commit when the branch
# already exists there, otherwise everything this branch adds to main.
BASE="${LOREGARDEN_TESTS_BASE:-}"
if [ -z "$BASE" ]; then
  BASE="$(git -C "$ROOT" rev-parse --verify --quiet "@{push}" || true)"
fi
if [ -z "$BASE" ]; then
  BASE="$(git -C "$ROOT" rev-parse --verify --quiet origin/main || true)"
fi

# Files every client test depends on, and that jest's module graph cannot see:
# the setup file and the moduleNameMapper mocks under src/test/ are wired in by
# config, not by import. Narrowing past these would be a guess.
WIDE_CHANGE=""
if [ -n "$BASE" ]; then
  WIDE_CHANGE="$(git -C "$ROOT" diff --name-only "$BASE...HEAD" -- \
    client/jest.config.cjs client/package.json client/package-lock.json \
    client/tsconfig.json client/tsconfig.app.json client/tsconfig.node.json \
    client/src/test || true)"
fi

# The second reporter counts finished files into the progress file; it does
# nothing when no capacity holder named one.
REPORTERS=(--reporters=default --reporters="$SCRIPT_DIR/jest_push_progress.cjs")

# The second, independent reading, paid only when the reporter recorded zero
# suites (run_and_judge decides). It asks one question: does the harness see any
# test file AT ALL? So it is jest's own testMatch selection, deliberately NOT
# narrowed by --changedSince — a probe carrying the same narrowing would return
# zero in exactly the cases the count is zero, and every ordinary "no test
# imports the component you edited" push would fail. That is R-5, the hostile
# guard that earns a --no-verify.
#
# Measured: `npx jest --listTests` prints 287 paths here, and one empty line
# while still exiting 0 when the selection resolves to nothing — so the number
# of paths is the reading, never the probe's exit code. Zero paths means a
# drifted testMatch, a renamed client/src, or a bogus pattern: the harness found
# nothing to run, which is an absence rather than an empty selection.
LIST_PROBE=(npx jest --listTests)

# `--bail` is jest's `-x`: stop at the first failing suite. Same reasoning as
# the server runner — the first failure is the one to read, and a client suite
# that has already gone red does not get more informative by finishing.
if [ -n "${LOREGARDEN_FULL_TESTS:-}" ]; then
  echo "pre-push: full jest run — LOREGARDEN_FULL_TESTS is set"
  progress_step "jest, full suite"
  run_and_judge jest -- "${TEST_NICE[@]}" npm test -- --bail --maxWorkers="$TEST_WORKERS" "${REPORTERS[@]}"
elif [ -z "$BASE" ]; then
  echo "pre-push: full jest run — no @{push} or origin/main to diff against"
  progress_step "jest, full suite"
  run_and_judge jest -- "${TEST_NICE[@]}" npm test -- --bail --maxWorkers="$TEST_WORKERS" "${REPORTERS[@]}"
elif [ -n "$WIDE_CHANGE" ]; then
  echo "pre-push: full jest run — shared config changed:"
  printf '  %s\n' $WIDE_CHANGE
  progress_step "jest, full suite"
  run_and_judge jest -- "${TEST_NICE[@]}" npm test -- --bail --maxWorkers="$TEST_WORKERS" "${REPORTERS[@]}"
else
  echo "pre-push: jest --changedSince=$BASE (CI runs the full suite; LOREGARDEN_FULL_TESTS=1 to run it here)"
  progress_step "jest, changed since push base"
  # --passWithNoTests stays: "no test imports what you changed" is a real answer
  # here, not a misconfiguration. What changed is who decides — jest's exit code
  # is identical for that case and for a harness that found no test file at all,
  # so the judge decides from the recorded suite count and the listing probe.
  run_and_judge jest -- "${TEST_NICE[@]}" npm test -- --bail --maxWorkers="$TEST_WORKERS" "${REPORTERS[@]}" --changedSince="$BASE" --passWithNoTests
fi
