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

ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLIENT_ROOT="$ROOT/client"

cd "$CLIENT_ROOT"

if [ ! -d node_modules ]; then
  echo "pre-push: client/node_modules missing (cd client && npm ci)." >&2
  exit 1
fi

echo "pre-push: npm run lint (oxlint) ..."
npm run lint

echo "pre-push: npx tsc -b ..."
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

if [ -n "${LOREGARDEN_FULL_TESTS:-}" ]; then
  echo "pre-push: full jest run — LOREGARDEN_FULL_TESTS is set"
  npm test
elif [ -z "$BASE" ]; then
  echo "pre-push: full jest run — no @{push} or origin/main to diff against"
  npm test
elif [ -n "$WIDE_CHANGE" ]; then
  echo "pre-push: full jest run — shared config changed:"
  printf '  %s\n' $WIDE_CHANGE
  npm test
else
  echo "pre-push: jest --changedSince=$BASE ..."
  echo "pre-push: (CI runs the full suite; LOREGARDEN_FULL_TESTS=1 to run it here)"
  # --passWithNoTests because "no test imports what you changed" is a real
  # answer here, not a misconfiguration. It is printed, never silent, and CI
  # still runs everything.
  npm test -- --changedSince="$BASE" --passWithNoTests
fi
