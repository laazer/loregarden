#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${LOREGARDEN_API_URL:-http://127.0.0.1:8000}"
WORKSPACE="${LOREGARDEN_WORKSPACE:-loregarden}"
POLL_SECONDS="${LOREGARDEN_SELF_IMPROVE_POLL_SECONDS:-5}"
STATE_DIR="$ROOT/.loregarden"
STATE_FILE="$STATE_DIR/self-improve-restart.state.json"
TRIGGER_FILE="$ROOT/server/.self-improve-restart"

mkdir -p "$STATE_DIR"
touch "$STATE_FILE"

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for self-improve restart watcher" >&2
  exit 1
fi

auth_args=()
if [[ -n "${LOREGARDEN_API_TOKEN:-}" ]]; then
  auth_args=(-H "Authorization: Bearer ${LOREGARDEN_API_TOKEN}")
fi

log() {
  printf '[self-improve-watch] %s\n' "$*"
}

wait_for_api() {
  until curl -fsS "${auth_args[@]}" "$API_URL/health" >/dev/null 2>&1; do
    log "waiting for API at $API_URL"
    sleep "$POLL_SECONDS"
  done
}

already_restarted() {
  local restart_key="$1"
  python3 - "$STATE_FILE" "$restart_key" <<'PY'
import json
import sys
from pathlib import Path

state_path = Path(sys.argv[1])
restart_key = sys.argv[2]
state = {"restarted_for": []}
if state_path.exists():
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        state = {"restarted_for": []}
print("yes" if restart_key in state.get("restarted_for", []) else "no")
PY
}

record_restart() {
  local restart_key="$1"
  python3 - "$STATE_FILE" "$restart_key" <<'PY'
import json
import sys
from pathlib import Path

state_path = Path(sys.argv[1])
restart_key = sys.argv[2]
state = {"restarted_for": []}
if state_path.exists():
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        state = {"restarted_for": []}
seen = state.setdefault("restarted_for", [])
if restart_key not in seen:
    seen.append(restart_key)
state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
PY
}

trigger_restart() {
  local restart_key="$1"
  local external_id="$2"
  local stage_key="$3"
  date -u +"%Y-%m-%dT%H:%M:%SZ" >"$TRIGGER_FILE"
  record_restart "$restart_key"
  log "triggered server reload for $external_id at stage $stage_key"
}

wait_for_api
log "watching workspace=$WORKSPACE poll=${POLL_SECONDS}s"

while true; do
  response="$(curl -fsS "${auth_args[@]}" \
    "$API_URL/api/system/self-improve-restart?workspace=${WORKSPACE}" || true)"

  if [[ -z "$response" ]]; then
    log "API unavailable; retrying"
    sleep "$POLL_SECONDS"
    continue
  fi

  read -r ready restart_key external_id stage_key blockers <<<"$(python3 -c '
import json
import sys

payload = json.loads(sys.argv[1])
ticket = (payload.get("human_gate_tickets") or [{}])[0]
print(
    "true" if payload.get("ready") else "false",
    payload.get("restart_key", ""),
    ticket.get("external_id", ""),
    ticket.get("workflow_stage_key", ""),
    ",".join(payload.get("blockers") or []),
)
' "$response")"

  if [[ "$ready" == "true" && -n "$restart_key" ]]; then
    if [[ "$(already_restarted "$restart_key")" == "yes" ]]; then
      sleep "$POLL_SECONDS"
      continue
    fi
    trigger_restart "$restart_key" "$external_id" "$stage_key"
  elif [[ -n "$blockers" && "$blockers" != "no_ticket_at_human_triage" ]]; then
    log "blocked: $blockers"
  fi

  sleep "$POLL_SECONDS"
done
