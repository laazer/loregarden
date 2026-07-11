#!/usr/bin/env bash
# Pre-push: mirror the "Client (TypeScript)" CI job exactly
# (.github/workflows/ci.yml) — lint, type check, test.
set -euo pipefail

# shellcheck source=hook-noninteractive.sh
source "$(cd "$(dirname "$0")" && pwd)/hook-noninteractive.sh"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
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

echo "pre-push: npm test (jest) ..."
npm test
