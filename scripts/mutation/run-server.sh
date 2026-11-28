#!/usr/bin/env bash
# The Python side of the mutation pilot (lg-build-verification-2639).
#
# Order is part of the spec: generate -> run -> collect -> reduce. A score
# reported before its control passed is a wrong-but-well-formed number, which
# is the failure mode this pilot exists to avoid, so the reducer — not this
# script — decides whether a score is reported at all, and exits non-zero when
# it withholds one.
#
# Run it through the capacity ledger, never bare:
#
#     task mutation:server
#
# mutmut reads [tool.mutmut] from the CWD and mutates a COPY at server/mutants/
# (runners/harness.py, change_cwd("mutants")), so this must run from server/ and
# never touches the working tree. `--control <file>` names the predictions,
# which AC-4 requires to have been written BEFORE the run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER="$REPO_ROOT/server"
OUT_DIR="${MUTATION_OUT_DIR:-$REPO_ROOT/mutation-out}"
CONTROL=""
CHECK_SELECTION=0
MUTANT_FILTER=""
while [ $# -gt 0 ]; do
  case "$1" in
    # Resolved to an absolute path: both scripts cd into server/ or client/
    # before the reducer runs, so a path the operator typed relative to the
    # repo root would not be found from there.
    --control) CONTROL="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
    # Run the configured test selection FROM THE SANDBOX and stop. This is the
    # check that is worth its 90 seconds: mutmut runs pytest with cwd=mutants,
    # a test that resolves paths from its own __file__ resolves them one
    # directory deeper there, and that failure lands in the BASELINE run — so
    # the campaign produces no .meta, no kill matrix and no score, after
    # however long it took to get there. No number of passing config tests can
    # see this; only a run from the copied tree can.
    --check-selection) CHECK_SELECTION=1; shift ;;
    # Run only the mutants matching a filter (fnmatch, per mutmut's own
    # collect_source_file_mutation_data), e.g.
    #   --mutants 'loregarden.services.integration_merge_budget.x__failed_turns__mutmut_*'
    # A whole pilot module is hundreds of mutants x its whole covering set,
    # which `--maxfail=0` makes unconditional — hours of exclusive heavy
    # capacity. This makes the PIPELINE provable in minutes without pretending
    # the campaign has been run.
    --mutants) MUTANT_FILTER="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# The worker budget this run was granted. Under capacity-run.sh this is the
# ledger's answer ($LOREGARDEN_CAPACITY_WORKERS); standalone it is half the
# cores. Running more than the grant paid for puts back the contention the
# queue exists to remove.
# shellcheck source=/dev/null
. "$REPO_ROOT/.lefthook/scripts/test-workers.sh"

KILL_MATRIX_DIR="$OUT_DIR/kill-matrix"
rm -rf "$KILL_MATRIX_DIR"
mkdir -p "$KILL_MATRIX_DIR" "$OUT_DIR"

# The plugin keys its rows by $MUTANT_UNDER_TEST, which mutmut sets per mutant
# (mutation/trampoline.py), and writes one file per mutant because each mutant
# is its own pytest process and a shared file would race.
export MUTMUT_KILL_MATRIX_DIR="$KILL_MATRIX_DIR"

# The MUTATED file, which is `only_mutate` — not `source_paths`. source_paths
# is the package mutmut copies into the sandbox (it must be the whole
# importable package or the tests cannot import it); only_mutate is the one
# pilot module, and it is that file that gets a .meta beside it.
PILOT_SOURCE="$(cd "$SERVER" && uv run python -c '
import tomllib, pathlib
cfg = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["tool"]["mutmut"]
only = cfg.get("only_mutate") or cfg["source_paths"]
print(only[0])
')"
echo "pilot module: $PILOT_SOURCE"
echo "workers: $TEST_WORKERS"

# STAGE THE PLUGIN WHERE mutmut CAN ACTUALLY COPY IT.
#
# `also_copy` entries resolve against mutmut's CWD (server/), it copies with
# shutil.copy2 and never mkdirs the destination parent, and only mutants/
# itself goes on sys.path. So the plugin has to be a top-level file in server/
# at run time; a nested path is either silently skipped (it does not exist
# from here) or a FileNotFoundError. See the comment on `also_copy` in
# server/pyproject.toml for the three mutmut 3.8.1 lines that force this.
#
# It is staged, not committed, so the repository keeps one copy of the plugin,
# under scripts/mutation/ where the rest of the pilot lives.
STAGED_PLUGIN="$SERVER/mutmut_killers.py"
cleanup() { rm -f "$STAGED_PLUGIN"; }
trap cleanup EXIT
cp "$REPO_ROOT/scripts/mutation/mutmut_killers.py" "$STAGED_PLUGIN"

# The configured selection, read from the one place that defines it.
read_selection() {
  (cd "$SERVER" && uv run python -c '
import tomllib, pathlib
cfg = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["tool"]["mutmut"]
print("\n".join(cfg["pytest_add_cli_args_test_selection"]))
')
}

if [ "$CHECK_SELECTION" = "1" ]; then
  cd "$SERVER"
  if [ ! -d mutants/tests ]; then
    echo "generating the sandbox (mutmut copies server/ to server/mutants/) ..."
    # Generation alone. The baseline run that follows it is the thing being
    # checked, so its exit status is not this command's answer.
    uv run mutmut run --max-children 1 >/dev/null 2>&1 || true
  fi
  [ -d mutants/tests ] || { echo "no sandbox at $SERVER/mutants — mutmut generated nothing" >&2; exit 1; }
  selection=()
  while IFS= read -r line; do [ -n "$line" ] && selection+=("$line"); done < <(read_selection)
  echo "running ${#selection[@]} selected test file(s) from the sandbox:"
  printf '  %s\n' "${selection[@]}"
  cd mutants
  # THE SANDBOX MUST BE THE THING UNDER TEST.
  #
  # This check is worthless unless `import loregarden` resolves HERE. The
  # server venv carries an editable install (a .pth holding an absolute path,
  # processed at interpreter start), so a bare pytest run from this directory
  # imports the REAL package and passes while telling you nothing — which is
  # exactly what it did, reporting 81 passed for a selection that then failed
  # mutmut's own baseline. mutmut avoids this in setup_source_paths by putting
  # mutants/ first and removing the original; PYTHONPATH is how we say the
  # same thing from outside, and the assertion below is how we prove it took.
  export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
  resolved="$(../.venv/bin/python -c 'import loregarden, pathlib; print(pathlib.Path(loregarden.__file__).resolve())')"
  case "$resolved" in
    */mutants/loregarden/*) echo "sandbox identity OK: loregarden resolves to $resolved" ;;
    *)
      echo "SANDBOX IDENTITY FAILED: loregarden resolves to $resolved," >&2
      echo "not to a path under server/mutants/. This check would pass against" >&2
      echo "the real package and prove nothing, so it refuses to run." >&2
      exit 1
      ;;
  esac
  if "${TEST_NICE[@]}" ../.venv/bin/python -m pytest "${selection[@]}" \
       -q -p no:randomly -p no:random-order; then
    echo "SELECTION OK: every selected file passes from server/mutants/."
    exit 0
  fi
  cat >&2 <<'WHY'

SELECTION NOT SANDBOX-RUNNABLE.

mutmut runs pytest with cwd=server/mutants/, so a test that resolves a path
from its own __file__ (Path(__file__).resolve().parents[N], readFileSync of a
sibling source) resolves it one directory deeper and reads something that is
not there. A file like that fails mutmut's BASELINE run, before any mutant is
tested, and the campaign then writes no .meta, no kill matrix and no score.

Either drop the offending file from [tool.mutmut]
pytest_add_cli_args_test_selection in server/pyproject.toml — saying there WHY
it is absent, because its tests then appear nowhere in the review list — or
choose a pilot module whose whole covering set passes from a copied tree.
WHY
  exit 1
fi

started=$(date +%s)
cd "$SERVER"
# `|| true`: mutmut exits non-zero whenever any mutant survived, which is the
# normal outcome of a pilot and not a failure of the run. The run's real
# failure modes are caught below — a missing .meta and an empty kill matrix
# both stop the collector — and the score is gated by the control after that.
mutmut_status=0
if [ -n "$MUTANT_FILTER" ]; then
  echo "mutant filter: $MUTANT_FILTER (a scoped run, NOT the campaign)"
  "${TEST_NICE[@]}" uv run mutmut run "$MUTANT_FILTER" || mutmut_status=$?
else
  "${TEST_NICE[@]}" uv run mutmut run || mutmut_status=$?
fi
finished=$(date +%s)
wall_clock=$((finished - started))
echo "mutmut wall clock: ${wall_clock}s"

# The one failure mode this key has is the tool quietly doing nothing, so say
# so here rather than letting it surface as an empty review list six hundred
# seconds downstream. copy_also_copy_files skips a missing path with a bare
# `continue`, which is a silent failure by any reading of this repo's rules.
# mutmut exits non-zero whenever any mutant survived, which is a pilot's normal
# outcome — so the status alone cannot say whether the RUN failed. A missing
# .meta can, and the most likely cause is a selection that cannot run from the
# sandbox, so say that rather than leaving the operator with mutmut's
# BadTestExecutionCommandsException and a wall of pytest arguments.
if [ ! -f "$SERVER/mutants/$PILOT_SOURCE.meta" ]; then
  echo "mutmut wrote no .meta for $PILOT_SOURCE (exit $mutmut_status)." >&2
  echo "The run did not reach mutant testing. The usual cause is a test in" >&2
  echo "[tool.mutmut] pytest_add_cli_args_test_selection that cannot run from" >&2
  echo "server/mutants/. Name it with:" >&2
  echo "    bash scripts/mutation/run-server.sh --check-selection" >&2
  exit 1
fi

[ -f "$SERVER/mutants/mutmut_killers.py" ] || {
  echo "mutmut did not copy the kill-matrix plugin into its sandbox:" >&2
  echo "  expected $SERVER/mutants/mutmut_killers.py" >&2
  echo "  check [tool.mutmut] also_copy names a TOP-LEVEL file that exists in $SERVER" >&2
  exit 1
}

uv run python "$REPO_ROOT/scripts/mutation/collect_server_run.py" \
  --server-root "$SERVER" \
  --source "$PILOT_SOURCE" \
  --kill-matrix-dir "$KILL_MATRIX_DIR" \
  --wall-clock-seconds "$wall_clock" \
  --out "$OUT_DIR/server-run.json"

reduce_args=(--side server --report "$OUT_DIR/server-run.json")
[ -n "$CONTROL" ] && reduce_args+=(--control "$CONTROL")
# Exits 1 when the control did not pass. That is the point: no score is
# reported for a side whose control failed, and a zero exit beside a null
# score would read to a caller as a clean run.
uv run python "$REPO_ROOT/scripts/mutation/kill_matrix.py" "${reduce_args[@]}" \
  | tee "$OUT_DIR/server-reduced.json"
