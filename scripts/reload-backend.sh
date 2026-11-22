#!/usr/bin/env bash
# Reload the dev server onto the code in this checkout, and say whether it worked.
#
#   ./scripts/reload-backend.sh
#
# Touches server/.self-improve-restart, then waits for the dev-server supervisor
# (server/loregarden/dev_supervisor.py) to record its answer to that touch in
# data/dev-server/status.json:
#
#   exit 0  ok       the new build is serving
#   exit 1  refused  the new build does not import; the previous one is still
#                    serving. The traceback is printed.
#   exit 1  failed   the worker did not come up; nothing may be serving
#   exit 1           no answer in time, or no server answering /health
#
# A bare `touch` still reloads, but reports nothing; a build that does not
# import used to take the control plane down that way, unnoticed.
#
# The status file belongs to the checkout the server runs from, which for a
# worktree is the primary checkout: run this from anywhere in the repository.
set -uo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
# The server runs from the primary checkout; a worktree's own data/ holds nothing.
common="$(git -C "$here" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
ROOT="$here"
if [[ -n "$common" && "$(basename "$common")" == ".git" ]]; then
  ROOT="$(dirname "$common")"
fi

SENTINEL="${LOREGARDEN_RELOAD_SENTINEL:-$ROOT/server/.self-improve-restart}"
STATE_DIR="${LOREGARDEN_DEV_STATE_DIR:-$ROOT/data/dev-server}"
TIMEOUT="${LOREGARDEN_RELOAD_TIMEOUT:-420}"
API_URL="${LOREGARDEN_API_URL:-http://127.0.0.1:${LOREGARDEN_DEV_PORT:-8000}}"

touch "$SENTINEL"

exec python3 - "$SENTINEL" "$STATE_DIR" "$TIMEOUT" "$API_URL" <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sentinel, state_dir, timeout, api_url = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
status_path = state_dir / "status.json"
trigger = sentinel.stat().st_mtime_ns
deadline = time.monotonic() + timeout
SETTLED = {"ok", "refused", "failed"}


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by someone else
        return True
    return True


def healthy():
    try:
        with urllib.request.urlopen(f"{api_url}/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def read():
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None  # mid-replace is impossible (os.replace); a hand edit is retried until the deadline


status = read()
if status is None or not alive(int(status.get("supervisor_pid", 0))):
    # An old `uvicorn --reload` server, or none: nothing will record an answer.
    # Health is the most this can check, and a reload takes a few seconds to start.
    print(f"No dev-server supervisor is recording reloads ({status_path}).", file=sys.stderr)
    print("Restart `task server` (or `task dev`) to get one. Checking /health instead.", file=sys.stderr)
    time.sleep(5)
    health_deadline = min(deadline, time.monotonic() + 120)
    while time.monotonic() < health_deadline:
        if healthy():
            print(f"unverified: {api_url}/health answers, but whether it runs the new build is unknown")
            sys.exit(0)
        time.sleep(2)
    print(f"FAILED: {api_url}/health is not answering after the reload. The server's output is in "
          "the terminal running it.", file=sys.stderr)
    sys.exit(1)

while time.monotonic() < deadline:
    status = read()
    if status and int(status.get("trigger", 0)) >= trigger and status.get("state") in SETTLED:
        state, detail = status["state"], status.get("detail", "")
        if state == "ok":
            print(f"ok: {detail}")
            sys.exit(0)
        print(f"{state.upper()}: {detail}", file=sys.stderr)
        for line in status.get("log_tail") or []:
            print(f"  {line}", file=sys.stderr)
        print(f"Full log: {status.get('log_file', '')}", file=sys.stderr)
        sys.exit(1)
    if status and not alive(int(status.get("supervisor_pid", 0))):
        print("FAILED: the dev-server supervisor exited before answering.", file=sys.stderr)
        sys.exit(1)
    time.sleep(1)

print(f"FAILED: no answer to the reload within {timeout:.0f}s ({status_path}).", file=sys.stderr)
sys.exit(1)
PY
