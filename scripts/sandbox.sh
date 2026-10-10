#!/usr/bin/env bash
# Run THIS checkout's server and client against a copy of production data.
#
#   task sandbox                 # fresh snapshot, server :8123, client :5174
#   task sandbox -- --seeded     # the production-shaped scenario instead
#   task sandbox -- --keep       # reuse the last snapshot or seed
#
# With no live database anywhere (CI, a cloud session, another workspace) it
# seeds automatically: loregarden.testing.prod_shape builds the same data shape
# from the integration-test factories, calibrated from the live database.
#
# Why: UI that is only ever seen on an empty dev database or a three-row fixture
# ships unusable — see CLAUDE.md "What it is for". This makes looking at real
# volume the default instead of a hand-built launch config.
#
# Safe by construction: the database and memory graphs are copied (SQLite backup
# API, source opened read-only) into data/sandbox/, and the server boots with
# LOREGARDEN_SANDBOX=1, so recovery, the reconcile timer and GitHub sync are off
# and nothing it does reaches main's runs, worktrees or memory. Its agents get
# an MCP URL naming this server, verified against its /health instance id.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SNAP="$ROOT/data/sandbox"
SERVER_PORT="${SANDBOX_SERVER_PORT:-8123}"
CLIENT_PORT="${SANDBOX_CLIENT_PORT:-5174}"

keep=0
seeded=0
for arg in "$@"; do
  case "$arg" in
    --keep) keep=1 ;;
    --seeded) seeded=1 ;;
    *) echo "sandbox: unknown argument $arg (--seeded, --keep)" >&2; exit 2 ;;
  esac
done

# shellcheck source=lib/primary-checkout.sh
source "$ROOT/scripts/lib/primary-checkout.sh"
if [[ "$seeded" == 0 ]] && ! resolve_primary_checkout >/dev/null; then
  echo "sandbox: no live database in this checkout's primary — using the seeded production shape"
  seeded=1
fi

ENV_FILE="$SNAP/env.sh"
if [[ "$keep" == 1 && -f "$ENV_FILE" ]]; then
  echo "sandbox: reusing the snapshot in $SNAP"
elif [[ "$seeded" == 1 ]]; then
  # The engine binds its database at import, so the seed runs already pointed
  # at the sandbox copies; `sandbox seed` refuses to run pointed anywhere else.
  mkdir -p "$SNAP"
  (cd "$ROOT/server" && LOREGARDEN_REPO_ROOT="$ROOT" LOREGARDEN_SANDBOX=1 \
    LOREGARDEN_DATABASE_URL="sqlite:///$SNAP/loregarden.db" \
    LOREGARDEN_MEMORY_SQLITE_URL="sqlite:///$SNAP/memory/memory.db" \
    LOREGARDEN_OBSIDIAN_VAULT_DIR="" \
    uv run loregarden sandbox seed --into "$SNAP") > "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  head -1 "$ENV_FILE"
else
  # The CLI resolves the primary checkout's database and memory config itself
  # (a linked worktree has no data of its own) — so leave LOREGARDEN_REPO_ROOT unset.
  mkdir -p "$SNAP"
  env -u LOREGARDEN_REPO_ROOT "$ROOT/scripts/loregarden-cli.sh" sandbox snapshot --into "$SNAP" \
    > "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  head -1 "$ENV_FILE"
fi
# shellcheck disable=SC1090
source "$ENV_FILE"
export LOREGARDEN_REPO_ROOT="$ROOT"
export LOREGARDEN_DEV_PORT="$SERVER_PORT"
# The agents this server starts must reach *this* server: main's default is the
# live one on :8000, and a URL inherited from the shell may be anything. Set from
# the port uvicorn is given below, so `--keep` with another port stays right; the
# server then proves it on boot (services/sandbox_endpoint.py) before any agent runs.
export LOREGARDEN_API_URL="http://127.0.0.1:$SERVER_PORT"
export LOREGARDEN_MCP_URL="$LOREGARDEN_API_URL/mcp"

server_pid=""
cleanup() { [[ -n "$server_pid" ]] && kill "$server_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# Both halves register as branch instances (sandbox-server, sandbox-client): the
# server's own registration stands down in a sandbox, so it never claims main.
(cd "$ROOT/server" && exec uv run loregarden instance run \
  --name sandbox-server --kind server --role branch --port "$SERVER_PORT" \
  --label "worktree=$ROOT" --label sandbox=1 \
  -- uvicorn loregarden.main:app --host 127.0.0.1 --port "$SERVER_PORT") &
server_pid=$!

echo "sandbox: server http://127.0.0.1:$SERVER_PORT  client http://127.0.0.1:$CLIENT_PORT"
cd "$ROOT/client"
LOREGARDEN_API_TARGET="http://127.0.0.1:$SERVER_PORT" uv run --project "$ROOT/server" \
  loregarden instance run \
  --name sandbox-client --kind client --role branch --port "$CLIENT_PORT" --health-path / \
  --label "worktree=$ROOT" --label "api=http://127.0.0.1:$SERVER_PORT" \
  -- ./node_modules/.bin/vite --host 127.0.0.1 --port "$CLIENT_PORT" --strictPort
