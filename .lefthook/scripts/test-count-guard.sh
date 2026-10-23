# shellcheck shell=bash
# The one seam both pre-push test scripts judge their runner through:
#
#   export LOREGARDEN_TEST_COUNT_FILE="$BASETEMP/test-count.json"
#   run_and_judge pytest -- env FOO=1 pytest -x -q ...
#
# It runs the command, then hands judge_test_run.py the runner's own exit code
# and lets it judge the count the runner's own collection recorded. The judge's
# verdict is what `run_and_judge` returns, so a run that collected nothing fails
# the push whatever the runner thought of it.
#
# Three things here are load-bearing, and each is a way the guard goes green:
#
# * **`set -e` is suspended around the runner.** Both scripts are
#   `set -euo pipefail`. A function body that invokes the runner as a bare
#   command aborts at the failing runner under `-e` and never reaches the judge,
#   so the push exits with pytest's own code and prints none of the four words.
#   The caller's `-e` is restored before returning, so the verdict still aborts
#   the script — with the JUDGE's code.
# * **A stale count is not evidence, and an inherited path is inert.** The count
#   file is removed before the runner starts, so a number from an earlier run
#   cannot be read as this one's — and the runner's own plugin then CLAIMS the
#   path by creating it exclusively, so the first collection to record owns it
#   and nothing can overwrite that number. This matters because the scripts
#   EXPORT the count path: every pytest the suite itself spawns inherits it, and
#   the suite does spawn pytest. Last-writer-wins made a 25-test run report 6.
#   "One writer per file" is a claim about processes, and only a claim a process
#   has to win can express it.
# * **The jest listing probe is paid only on a zero count** (`$LIST_PROBE`, set
#   by client-tests.sh over the same selection). Zero suites with files on disk
#   is an empty selection; zero files is a harness that found nothing.

_TCG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 0 when the recorded count is zero. Reading the count file is not parsing the
# runner: this is the number the runner's own collection wrote, in the one shape
# its plugin and its reporter both write. Anything else (absent, truncated,
# malformed) is not a zero, and the judge resolves it as an absence.
_tcg_count_is_zero() {
  local file="$1"
  [ -n "$file" ] && [ -f "$file" ] || return 1
  grep -Eq '"count"[[:space:]]*:[[:space:]]*0[[:space:]]*(,|})' "$file"
}

# How many test files the harness could see, over the same selection. One empty
# line is jest's own answer for "nothing", and it exits 0 either way, so the
# count of non-blank lines is the reading — never the probe's exit code.
_tcg_listed_files() {
  local out=""
  out="$("$@" 2>/dev/null)" || true
  printf '%s\n' "$out" | grep -c '[^[:space:]]' || true
}

run_and_judge() {
  local runner="$1"
  shift
  if [ "${1:-}" = "--" ]; then
    shift
  fi

  # Clear the path so the runner's plugin can claim it by creating it. An
  # unremovable file is left as it is: the plugin then loses its claim and
  # records nothing, which the judge reports as could_not_run rather than
  # reading an earlier run's number as this one's.
  local count_file="${LOREGARDEN_TEST_COUNT_FILE:-}"
  if [ -n "$count_file" ]; then
    # silent-ok: nothing to clear is the normal first run; a path that will not
    # go then fails the plugin's claim, which the judge reports
    rm -f "$count_file" 2>/dev/null || true
  fi

  local had_errexit=""
  case $- in
    *e*) had_errexit="1" ;;
  esac
  set +e

  "$@"
  local rc=$?

  local listed=""
  if [ "$runner" = "jest" ] && _tcg_count_is_zero "$count_file"; then
    if declare -p LIST_PROBE >/dev/null 2>&1; then
      listed="$(_tcg_listed_files "${LIST_PROBE[@]}")"
    else
      listed="0"
    fi
  fi

  # The judge composes the board's report beside the progress file; the shell
  # puts it in place, so the progress file gains no second writer.
  local report=""
  if declare -F progress_report_path >/dev/null 2>&1; then
    report="$(progress_report_path)"
  fi

  local judge=(python3 "$_TCG_DIR/judge_test_run.py" --runner "$runner" --exit "$rc")
  if [ -n "$listed" ]; then
    judge+=(--listed-files "$listed")
  fi
  if [ -n "$report" ]; then
    judge+=(--report-file "$report")
  fi
  "${judge[@]}"
  local verdict=$?

  if [ -n "$report" ]; then
    progress_report_commit "$report"
  fi
  if [ -n "$had_errexit" ]; then
    set -e
  fi
  return "$verdict"
}
