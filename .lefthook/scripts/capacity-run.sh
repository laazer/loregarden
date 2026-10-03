#!/usr/bin/env bash
# Run a command while holding host capacity on the loregarden ledger:
#
#   capacity-run.sh <label> [capacity flags…] -- <command…>
#   capacity-run.sh "pre-push server-tests" --footprint heavy --workspace loregarden -- bash x.sh
#
# Every repo loregarden tracks pushes from the same machine. Without this, three
# pre-push suites start together, each sized to half the cores, and fail on time
# rather than behaviour. With it they queue: the command starts when its
# footprint fits, and LOREGARDEN_CAPACITY_WORKERS tells it how many workers the
# grant pays for.
#
# Waiting in line is not a failure — `loregarden capacity run` prints the line
# and waits for as long as it keeps moving, however long that is. Six heavy
# suites ahead is hours, and an orderly line is never a reason to fail a push.
# Two things are, and they end differently:
#
# The line stopped moving: nothing ahead has finished for
# LOREGARDEN_CAPACITY_STALL_SECONDS (default 3600 — above the slowest pre-push
# suite the ledger has seen), usually a holder that hung. The CLI exits 75 having
# said who is holding, how many are ahead and the estimate. The ledger is fine,
# so this is not retried — retrying would only rejoin at the back:
#   - at a terminal, ask: [p] proceed without reserving, [r] queue again,
#     anything else stops;
#   - with no terminal (an agent), fail with exit 75.
#
# The ledger is unusable (database locked, the CLI broken, a footprint the
# machine can never fit) — any other exit before the command starts. Then:
#   - retry with exponential backoff (1s, 2s, 4s … capped at 60s);
#   - at a terminal, after LOREGARDEN_CAPACITY_RETRIES failures (default 6) or
#     the hard cap, ask: [p] proceed without reserving, [r] wait and retry,
#     anything else stops;
#   - with no terminal (an agent), keep retrying until
#     LOREGARDEN_CAPACITY_HARD_CAP_SECONDS (default 3600) have passed since the
#     first failure, then fail. Time spent queued before it does not count.
#
# LOREGARDEN_CAPACITY=off runs the command unreserved, and says so. It skips the
# reservation only — the command itself still runs.
#
# The CLI comes from the PRIMARY checkout, not this one: a branch that adds a
# migration is refused by its own CLI (the shared-database guard), which would
# make every such push fail here. A primary checkout that predates the command
# runs the command unreserved with a warning — retrying cannot fix an old build.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hook-noninteractive.sh
source "$SCRIPT_DIR/hook-noninteractive.sh"

warn() { printf 'capacity-run: %s\n' "$*" >&2; }

if [ $# -lt 1 ]; then
  warn "usage: capacity-run.sh <label> [capacity flags…] -- <command…>"
  exit 2
fi
label="$1"
shift
cli_args=()
while [ $# -gt 0 ] && [ "$1" != "--" ]; do
  cli_args+=("$1")
  shift
done
[ "${1:-}" = "--" ] && shift
if [ $# -eq 0 ]; then
  warn "no command given after --"
  exit 2
fi

if [ "${LOREGARDEN_CAPACITY:-}" = "off" ]; then
  warn "LOREGARDEN_CAPACITY=off — running '$label' WITHOUT reserving capacity."
  exec "$@"
fi

# LOREGARDEN_CAPACITY_CLI names the CLI outright — a specific checkout's
# scripts/loregarden-cli.sh, or a stand-in under test.
cli="${LOREGARDEN_CAPACITY_CLI:-}"
if [ -z "$cli" ]; then
  ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
  # shellcheck source=../../scripts/lib/primary-checkout.sh
  source "$ROOT/scripts/lib/primary-checkout.sh"
  primary="$(resolve_primary_checkout)" || primary="$ROOT"
  cli="$primary/scripts/loregarden-cli.sh"
  if [ ! -f "$primary/server/loregarden/cli/capacity.py" ]; then
    warn "WARNING: $primary predates 'loregarden capacity'; running '$label' WITHOUT reserving."
    warn "Update that checkout to main to queue for capacity."
    exec "$@"
  fi
  if ! grep -q -- '--stall-timeout' "$primary/server/loregarden/cli/capacity.py"; then
    # Its --max-wait counts time spent in line: a push far enough back gives up
    # before its turn, and reports it as a ledger failure.
    warn "WARNING: $primary predates queue-stall timeouts; waiting in line counts against"
    warn "its --max-wait. Update that checkout to main."
    stall_flag="--max-wait"
  fi
fi
stall_flag="${stall_flag:---stall-timeout}"

repo="$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")"
branch="$(git branch --show-current 2>/dev/null || true)"
full_label="$label · $repo${branch:+@$branch}"

hard_cap="${LOREGARDEN_CAPACITY_HARD_CAP_SECONDS:-3600}"
stall="${LOREGARDEN_CAPACITY_STALL_SECONDS:-3600}"
quiet_retries="${LOREGARDEN_CAPACITY_RETRIES:-6}"
# `loregarden capacity run`'s code for "still queued, but the line stopped moving".
EXIT_QUEUE_STALLED=75
started_file="${TMPDIR:-/tmp}/lg-capacity-$$-started"
trap 'rm -f "$started_file"' EXIT

at_terminal() { [ -t 0 ]; }

# When the current run of ledger failures began; empty while there is none.
failing_since=""
failures=0
delay=1
while :; do
  rm -f "$started_file"
  "$cli" capacity run --label "$full_label" ${cli_args[@]+"${cli_args[@]}"} \
    "$stall_flag" "$stall" --started-file "$started_file" -- "$@"
  rc=$?
  if [ -e "$started_file" ]; then
    exit "$rc" # the command ran; its status is ours
  fi

  if [ "$rc" -eq "$EXIT_QUEUE_STALLED" ]; then
    warn "'$label' was still queued when the line stopped moving: nothing ahead finished"
    warn "for ${stall}s (position, estimate and holder above). The ledger is working."
    if at_terminal; then
      printf 'capacity-run: [p] proceed without reserving  [r] queue again  [other] stop: ' >&2
      answer=""
      read -r answer || answer=""
      case "$answer" in
        p | P)
          warn "proceeding WITHOUT a capacity reservation."
          exec "$@"
          ;;
        r | R)
          failing_since=""
          failures=0
          delay=1
          continue
          ;;
      esac
      warn "stopped."
    else
      warn "Push again to rejoin the line, or free the holder named above."
    fi
    exit "$EXIT_QUEUE_STALLED"
  fi

  failures=$((failures + 1))
  [ -z "$failing_since" ] && failing_since=$SECONDS
  failing=$((SECONDS - failing_since))
  warn "could not reserve capacity for '$label' (attempt $failures, failing for ${failing}s, exit $rc)."

  if at_terminal && { [ "$failures" -ge "$quiet_retries" ] || [ "$failing" -ge "$hard_cap" ]; }; then
    printf 'capacity-run: [p] proceed without reserving  [r] wait and retry  [other] stop: ' >&2
    answer=""
    read -r answer || answer=""
    case "$answer" in
      p | P)
        warn "proceeding WITHOUT a capacity reservation."
        exec "$@"
        ;;
      r | R)
        failing_since=""
        failures=0
        delay=1
        continue
        ;;
      *)
        warn "stopped."
        exit 1
        ;;
    esac
  fi

  if ! at_terminal && [ "$failing" -ge "$hard_cap" ]; then
    warn "gave up after ${failing}s of ledger failures (LOREGARDEN_CAPACITY_HARD_CAP_SECONDS=$hard_cap)."
    warn "The ledger error is printed above. LOREGARDEN_CAPACITY=off runs without reserving."
    exit 1
  fi
  sleep "$delay"
  delay=$((delay * 2))
  [ "$delay" -gt 60 ] && delay=60
done
