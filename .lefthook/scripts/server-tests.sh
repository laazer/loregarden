#!/usr/bin/env bash
# Pre-push: the "Server (Python)" CI job (.github/workflows/ci.yml) — ruff lint,
# ruff format check, pytest — with pytest narrowed to the tests the pushed
# commits can actually break.
#
# The lint steps still run over everything; they cost seconds. pytest does not:
# the full suite is five minutes on an idle machine and worse under load, which
# is how a pre-push hook teaches people to reach for --no-verify.
# select_pytest_targets.py walks the import graph and falls back to the full
# suite whenever it cannot map a change (see its docstring — the bias is toward
# running too much).
#
# This means a green push no longer *proves* green CI. CI still runs the whole
# suite on every PR, so a miss costs a slower signal, not an unguarded merge.
# LOREGARDEN_FULL_TESTS=1 forces the full run; LOREGARDEN_TESTS_BASE=<ref>
# overrides the base, to ask what a given range would run.
set -euo pipefail

# shellcheck source=hook-noninteractive.sh
source "$(cd "$(dirname "$0")" && pwd)/hook-noninteractive.sh"
# shellcheck source=test-workers.sh
source "$(cd "$(dirname "$0")" && pwd)/test-workers.sh"
# shellcheck source=push-progress.sh
source "$(cd "$(dirname "$0")" && pwd)/push-progress.sh"
# shellcheck source=test-count-guard.sh
source "$(cd "$(dirname "$0")" && pwd)/test-count-guard.sh"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY_ROOT="$ROOT/server"

cd "$PY_ROOT"

if [ -x ".venv/bin/python" ] && ".venv/bin/python" -c "import pytest" 2>/dev/null; then
  RUN=(".venv/bin/python" "-m")
  RUFF_CMD=(".venv/bin/ruff")
elif command -v uv >/dev/null 2>&1; then
  RUN=(uv run --extra dev python -m)
  RUFF_CMD=(uv run --extra dev ruff)
else
  echo "pre-push: need server/.venv (with pytest) or uv on PATH." >&2
  echo "Run: cd server && uv sync --extra dev" >&2
  exit 1
fi

# A private temp root for this run, because pytest's own cleanup is not safe
# against a concurrent session. `_pytest.tmpdir` garbage-collects the older
# `pytest-of-<user>/pytest-N` directories it finds at session start — so two
# pytest runs sharing the default root will delete each other's trees, and the
# loser dies in `pytest_sessionfinish`:
#
#   shutil.py: if os.path.samestat(orig_st, os.fstat(dirfd)):
#   FileNotFoundError: [Errno 2] No such file or directory
#
# `rmtree(basetemp, ignore_errors=True)` does not cover that `fstat`, so the
# session exits non-zero *after* reporting every test passed — a green suite and
# a rejected push, which reads as a flaky test and is not one. This machine runs
# the control plane against its own tickets, so a second pytest is ordinary
# rather than exotic. Observed twice on one branch before it was diagnosed.
#
# Naming a basetemp fixes it twice over. The root is unique per invocation, so
# no other session can prune it — and `pytest_sessionfinish` skips that cleanup
# entirely when `--basetemp` was given (`_given_basetemp is None` guards it), so
# the crashing call is not reached at all.
#
# That also means pytest leaves the tree behind, and this trap is the only thing
# that removes it — not a belt-and-braces extra. It must never fail the push on
# its own: a temp dir that outlives a run costs disk, a cleanup that raises
# costs the push.
RUN_TEMP="$(mktemp -d "${TMPDIR:-/tmp}/loregarden-pytest-XXXXXX")"
# pytest OWNS its basetemp: given one, `_pytest.tmpdir` rmtree's it at the first
# `tmp_path` use, which is after collection. So the count file lives beside that
# directory rather than inside it; in it, a serial run would have its recorded
# count deleted by the first test that asks for a temp dir, and the judge would
# report COULD NOT RUN for a suite that ran.
BASETEMP="$RUN_TEMP/pytest"
mkdir -p "$BASETEMP"
trap 'rc=$?; progress_finish "$rc"; rm -rf "$RUN_TEMP" 2>/dev/null || true' EXIT
progress_begin "server-tests" 5

# Fail fast. `-x` stops at the first failure instead of collecting every one,
# because the first failure is the only one worth reading: a cascade dominates
# the output and picks the wrong suspect. One recorded run here showed 54
# "unable to find an element" errors downstream of 4 real timeouts, and counting
# symptoms sent the diagnosis to the wrong file.
#
# It also bounds the cost of a bad push on a loaded box. The full-suite fallback
# is ~45 minutes idle and over 90 under load; stopping at the first red returns
# the answer in the time it takes to reach it.
#
# CI still runs to completion without `-x`, so the total count is never lost —
# this only changes which report arrives first, and how long a broken push waits
# to be told.

progress_step "ruff check"
"${RUFF_CMD[@]}" check .

progress_step "ruff format --check"
"${RUFF_CMD[@]}" format --check .

progress_step "ruff on scripts outside server/"
bash "$ROOT/.lefthook/scripts/py-scripts-ruff.sh" check
bash "$ROOT/.lefthook/scripts/py-scripts-ruff.sh" format --check

# Base for "what is being pushed": the remote-tracking commit when the branch
# already exists there, otherwise everything this branch adds to main.
BASE="${LOREGARDEN_TESTS_BASE:-}"
if [ -z "$BASE" ]; then
  BASE="$(git -C "$ROOT" rev-parse --verify --quiet "@{push}" || true)"
fi
if [ -z "$BASE" ]; then
  BASE="$(git -C "$ROOT" rev-parse --verify --quiet origin/main || true)"
fi

progress_step "select tests"
TARGETS=""
SELECT_REASON=""
if [ -n "${LOREGARDEN_FULL_TESTS:-}" ]; then
  SELECT_REASON="LOREGARDEN_FULL_TESTS is set"
elif [ -z "$BASE" ]; then
  SELECT_REASON="no @{push} or origin/main to diff against"
else
  ERR_FILE="$(mktemp)"
  set +e
  # The server interpreter, not `python3`: a 3.9 on PATH cannot parse a module
  # using `match`, and an import graph missing its edges selects too little.
  TARGETS="$(bash "$ROOT/.lefthook/scripts/server_python.sh" "$ROOT/.lefthook/scripts/select_pytest_targets.py" --repo "$ROOT" --base "$BASE" 2>"$ERR_FILE")"
  SELECT_STATUS=$?
  set -e
  if [ $SELECT_STATUS -ne 0 ]; then
    TARGETS=""
    SELECT_REASON="$(cat "$ERR_FILE")"
  fi
  rm -f "$ERR_FILE"
fi

# The plugin records what collection found, and counts tests into the progress
# file. The count is always recorded; the progress half does nothing when no
# capacity holder named a file.
export PYTHONPATH="$ROOT/.lefthook/scripts${PYTHONPATH:+:$PYTHONPATH}"

# Where this run's collection records how many tests it found. Private to this
# invocation (under $RUN_TEMP, removed by the trap above), never the progress
# file, and written only by the plugin. run_and_judge reads it after pytest
# exits: a run that collected nothing fails this push whatever pytest's own
# code was, and a run that recorded no count at all fails it differently.
export LOREGARDEN_TEST_COUNT_FILE="$RUN_TEMP/test-count.json"

if [ -z "$TARGETS" ]; then
  echo "pre-push: full pytest run — ${SELECT_REASON:-selection unavailable}"
  progress_step "pytest, full suite"
  run_and_judge pytest -- env LOREGARDEN_REPO_ROOT="$ROOT" "${TEST_NICE[@]}" "${RUN[@]}" \
    pytest -x -q -n "$TEST_WORKERS" --basetemp="$BASETEMP" -p pytest_push_progress
else
  # Paths arrive repo-relative; pytest runs from server/.
  FILES=()
  while IFS= read -r target; do
    [ -n "$target" ] || continue
    rel="${target#server/}"
    if [ ! -f "$PY_ROOT/$rel" ]; then
      # A path pytest cannot collect is reported as a pass, not an error, so a
      # stale selection would look exactly like a green suite. This check is
      # earlier and narrower than the judge, and stays as it is.
      echo "pre-push: selector named a missing file: $target" >&2
      exit 1
    fi
    FILES+=("$rel")
  done <<< "$TARGETS"

  echo "pre-push: ${#FILES[@]} test file(s) reach the pushed changes:"
  printf '  %s\n' "${FILES[@]}"
  echo "pre-push: (CI runs the full suite; LOREGARDEN_FULL_TESTS=1 to run it here)"
  progress_step "pytest on ${#FILES[@]} file(s)"
  run_and_judge pytest -- env LOREGARDEN_REPO_ROOT="$ROOT" "${TEST_NICE[@]}" "${RUN[@]}" \
    pytest -x -q -n "$TEST_WORKERS" --basetemp="$BASETEMP" -p pytest_push_progress "${FILES[@]}"
fi
