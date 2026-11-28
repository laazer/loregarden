#!/usr/bin/env bash
# The TypeScript side of the mutation pilot (lg-build-verification-2639).
#
# Run it through the capacity ledger, never bare:
#
#     task mutation:client
#
# As on the server side, the reducer decides whether a score is reported and
# exits non-zero when it withholds one.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLIENT="$REPO_ROOT/client"
OUT_DIR="${MUTATION_OUT_DIR:-$REPO_ROOT/mutation-out}"
CONTROL=""
while [ $# -gt 0 ]; do
  case "$1" in
    # Resolved to an absolute path: both scripts cd into server/ or client/
    # before the reducer runs, so a path the operator typed relative to the
    # repo root would not be found from there.
    --control) CONTROL="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# shellcheck source=/dev/null
. "$REPO_ROOT/.lefthook/scripts/test-workers.sh"

mkdir -p "$OUT_DIR"

# THE GRANT IS SPENT ONCE, NOT N TIMES OVER.
#
# client/jest.config.cjs:24-27 already reads $LOREGARDEN_CAPACITY_WORKERS and
# sets jest's maxWorkers from it. Stryker runs N workers, and each one runs
# jest. Left alone that is N x N against a grant of N, which does not fail
# honestly — it presents as flake under load, the most expensive way for a
# capacity bug to show up.
#
# Stryker's concurrency takes the grant here. Forcing jest to ONE worker
# inside each Stryker worker is NOT done on this command line: the jest
# runner's options are config-file only, and `stryker run --jest.config` is
# rejected with "unknown option" in Stryker 10. It lives in
# client/stryker.config.json under jest.config.maxWorkers, which the runner
# layers over jest.config.cjs. Also unset the variable here so a jest started
# any other way inside this run cannot read the whole grant.
STRYKER_CONCURRENCY="$TEST_WORKERS"
unset LOREGARDEN_CAPACITY_WORKERS
echo "stryker concurrency: $STRYKER_CONCURRENCY (jest: 1 worker inside each, from stryker.config.json)"

cd "$CLIENT"

# WHERE STRYKER ACTUALLY WRITES, read from the config that decides it.
#
# The json reporter's path has no CLI form (there is no --jsonReporter.fileName
# in Stryker 10), so client/stryker.config.json is the only place it is set and
# $MUTATION_OUT_DIR cannot move it. Deriving the path here instead of rebuilding
# it from $OUT_DIR keeps the freshness check below pointed at the file Stryker
# writes rather than at one it never will. $MUTATION_OUT_DIR still chooses where
# the REDUCED report goes, which is this script's own output.
REPORT="$(python3 -c '
import json, pathlib, sys
config = json.loads(pathlib.Path("stryker.config.json").read_text(encoding="utf-8"))
print(pathlib.Path(config["jsonReporter"]["fileName"]).resolve())
')"

# A STALE REPORT MUST NOT BE READ AS THIS RUN'S.
#
# mutation-out/ is gitignored and persists between runs, and the reporter path
# is the same every time. Without this, a Stryker that dies before writing
# anything leaves the PREVIOUS run's report in place, the existence check
# below passes, record_wall_clock.py stamps this run's seconds onto it —
# erasing the last sign it is stale — and the reducer prints a complete
# mutation score for a run that never happened, with exit 0.
#
# Removing it first is what makes "the file exists" mean "this run wrote it".
rm -f "$REPORT"

started=$(date +%s)
# Stryker exits non-zero whenever a mutant SURVIVED, which is a pilot's normal
# outcome, so the status alone cannot say whether the run failed. The question
# that can is whether this run produced a report of its own — hence the rm
# above. The status is still captured, and reported beside the missing report,
# because a bare `|| true` once turned "stryker refused the command line" into
# "stryker wrote no json report" and pointed the operator at the wrong cause.
stryker_status=0
"${TEST_NICE[@]}" npx stryker run --concurrency "$STRYKER_CONCURRENCY" || stryker_status=$?
finished=$(date +%s)
echo "stryker wall clock: $((finished - started))s (exit $stryker_status)"

[ -f "$REPORT" ] || {
  echo "stryker wrote no json report at $REPORT (stryker exited $stryker_status)" >&2
  if [ "$stryker_status" -ne 0 ]; then
    echo "  stryker itself failed — read its output above; the missing report is a symptom" >&2
  fi
  echo "  refusing to reduce: there is no report from THIS run, and a score from" >&2
  echo "  an older one would be indistinguishable from a real result." >&2
  exit 1
}

# Stryker's json reporter does not reliably carry a `performance` block, and
# a summed-absent block is 0.0 — a wall clock of zero beside a sixteen-minute
# run. This script is what actually watched the clock, so it records what it
# measured. The server side says the same fact the same way, through
# collect_server_run.py --wall-clock-seconds.
python3 "$REPO_ROOT/scripts/mutation/record_wall_clock.py" "$REPORT" "$((finished - started))"

reduce_args=(--side client --report "$REPORT")
[ -n "$CONTROL" ] && reduce_args+=(--control "$CONTROL")
python3 "$REPO_ROOT/scripts/mutation/kill_matrix.py" "${reduce_args[@]}" \
  | tee "$OUT_DIR/client-reduced.json"
