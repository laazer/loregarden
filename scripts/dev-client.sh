#!/usr/bin/env bash
# The dev client, registered in the local instance registry as loregarden's main
# client (`loregarden-client`), so the Instances page lists it and the launcher
# never hands its port to a branch instance.
#
# It proxies to LOREGARDEN_API_TARGET when set, otherwise to the main server the
# registry names (`task server` registers itself, on whatever port it took),
# otherwise to the default http://127.0.0.1:8000.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/client"

# Same host variable as scripts/dev-server.sh and vite.config.ts. Explicit, so
# the port registered is the port bound: Vite's own default would move to the
# next free port, and `localhost` may bind only ::1 where a probe asks 127.0.0.1.
HOST="${LOREGARDEN_DEV_HOST:-127.0.0.1}"
PORT="${LOREGARDEN_CLIENT_PORT:-5173}"

if ! command -v uv >/dev/null 2>&1; then
  echo "dev-client: uv not found — running unregistered, proxying to ${LOREGARDEN_API_TARGET:-the default http://127.0.0.1:8000}" >&2
  exec npm run dev -- --host "$HOST" --port "$PORT" --strictPort
fi

if [[ -z "${LOREGARDEN_API_TARGET:-}" ]]; then
  if target="$(uv run --project "$ROOT/server" python -m lore_eden.instances --project loregarden url main)"; then
    export LOREGARDEN_API_TARGET="$target"
  else
    echo "dev-client: no main server registered — proxying to http://127.0.0.1:8000" >&2
    export LOREGARDEN_API_TARGET="http://127.0.0.1:8000"
  fi
fi
echo "dev-client: proxying to $LOREGARDEN_API_TARGET" >&2

# Vite itself, not `npm run dev`: npm takes the SIGTERM the wrapper forwards and
# does not pass it on, so the client would outlive its registration.
if [[ ! -x node_modules/.bin/vite ]]; then
  echo "dev-client: client/node_modules is missing — run \`npm ci\` in client/ first" >&2
  exit 1
fi

exec uv run --project "$ROOT/server" loregarden instance run \
  --name client --kind client --host "$HOST" --port "$PORT" --health-path / \
  --label "worktree=$ROOT" --label "api=$LOREGARDEN_API_TARGET" \
  -- ./node_modules/.bin/vite --host "$HOST" --port "$PORT" --strictPort
