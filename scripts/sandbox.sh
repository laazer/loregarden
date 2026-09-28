#!/usr/bin/env bash
# Run THIS checkout's server and client against a copy of production data.
#
#   task sandbox                 # fresh snapshot, server :8123, client :5174
#   task sandbox -- --keep       # reuse the last snapshot
#
# Why: UI that is only ever seen on an empty dev database or a three-row fixture
# ships unusable — see CLAUDE.md "What it is for". This makes looking at real
# volume the default instead of a hand-built launch config.
#
# Safe by construction: the database and memory graphs are copied (SQLite backup
# API, source opened read-only) into data/sandbox/, and the server boots with
# LOREGARDEN_SANDBOX=1, so recovery, the reconcile timer and GitHub sync are off
# and nothing it does reaches main's runs, worktrees or memory.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SNAP="$ROOT/data/sandbox"
SERVER_PORT="${SANDBOX_SERVER_PORT:-8123}"
CLIENT_PORT="${SANDBOX_CLIENT_PORT:-5174}"

keep=0
for arg in "$@"; do
  case "$arg" in
    --keep) keep=1 ;;
    *) echo "sandbox: unknown argument $arg (only --keep)" >&2; exit 2 ;;
  esac
done

ENV_FILE="$SNAP/env.sh"
if [[ "$keep" == 1 && -f "$ENV_FILE" ]]; then
  echo "sandbox: reusing the snapshot in $SNAP"
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

server_pid=""
cleanup() { [[ -n "$server_pid" ]] && kill "$server_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd "$ROOT/server" && exec uv run uvicorn loregarden.main:app --host 127.0.0.1 --port "$SERVER_PORT") &
server_pid=$!

echo "sandbox: server http://127.0.0.1:$SERVER_PORT  client http://localhost:$CLIENT_PORT"
cd "$ROOT/client"
LOREGARDEN_API_TARGET="http://127.0.0.1:$SERVER_PORT" npm run dev -- --port "$CLIENT_PORT" --strictPort
