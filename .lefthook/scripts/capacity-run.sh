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
# and waits. A failure is the ledger itself being unusable (database locked, the
# CLI broken, a footprint the machine can never fit). Then:
#   - retry with exponential backoff (1s, 2s, 4s … capped at 60s);
#   - at a terminal, after LOREGARDEN_CAPACITY_RETRIES failures (default 6) or
#     the hard cap, ask: [p] proceed without reserving, [r] wait and retry,
#     anything else stops;
#   - with no terminal (an agent), keep retrying until
#     LOREGARDEN_CAPACITY_HARD_CAP_SECONDS (default 3600), then fail.
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
fi

repo="$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")"
branch="$(git branch --show-current 2>/dev/null || true)"
# Where it runs: the branch alone when the worktree is named after it (the
# usual case — "lg-x-e33a13@claude/lg-x-e33a13" said one thing twice), else
# worktree@branch. services/capacity_label.py reads both forms back.
if [ -z "$branch" ]; then
  place="$repo"
elif [ "${branch##*/}" = "$repo" ]; then
  place="$branch"
else
  place="$repo@$branch"
fi
full_label="$label · $place"

hard_cap="${LOREGARDEN_CAPACITY_HARD_CAP_SECONDS:-3600}"
quiet_retries="${LOREGARDEN_CAPACITY_RETRIES:-6}"
started_file="${TMPDIR:-/tmp}/lg-capacity-$$-started"
trap 'rm -f "$started_file"' EXIT

at_terminal() { [ -t 0 ]; }

start=$SECONDS
failures=0
delay=1
while :; do
  rm -f "$started_file"
  remaining=$((hard_cap - (SECONDS - start)))
  [ "$remaining" -lt 1 ] && remaining=1
  # The CLI runs from its own server/ directory, and a primary checkout older
  # than LOREGARDEN_CALLER_CWD starts the command there too — where a relative
  # `.lefthook/scripts/…` does not exist. Start it from here, whatever the CLI.
  "$cli" capacity run --label "$full_label" ${cli_args[@]+"${cli_args[@]}"} \
    --max-wait "$remaining" --started-file "$started_file" \
    -- /bin/sh -c 'cd "$1" && shift && exec "$@"' capacity-run "$PWD" "$@"
  rc=$?
  if [ -e "$started_file" ]; then
    exit "$rc" # the command ran; its status is ours
  fi

  failures=$((failures + 1))
  elapsed=$((SECONDS - start))
  warn "could not reserve capacity for '$label' (attempt $failures, ${elapsed}s, exit $rc)."

  if at_terminal && { [ "$failures" -ge "$quiet_retries" ] || [ "$elapsed" -ge "$hard_cap" ]; }; then
    printf 'capacity-run: [p] proceed without reserving  [r] wait and retry  [other] stop: ' >&2
    answer=""
    read -r answer || answer=""
    case "$answer" in
      p | P)
        warn "proceeding WITHOUT a capacity reservation."
        exec "$@"
        ;;
      r | R)
        start=$SECONDS
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

  if ! at_terminal && [ "$elapsed" -ge "$hard_cap" ]; then
    warn "gave up after ${elapsed}s (LOREGARDEN_CAPACITY_HARD_CAP_SECONDS=$hard_cap)."
    warn "The ledger error is printed above. LOREGARDEN_CAPACITY=off runs without reserving."
    exit 1
  fi
  sleep "$delay"
  delay=$((delay * 2))
  [ "$delay" -gt 60 ] && delay=60
done
