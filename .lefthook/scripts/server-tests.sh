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
BASETEMP="$(mktemp -d "${TMPDIR:-/tmp}/loregarden-pytest-XXXXXX")"
trap 'rm -rf "$BASETEMP" 2>/dev/null || true' EXIT

echo "pre-push: ruff check ..."
"${RUFF_CMD[@]}" check .

echo "pre-push: ruff format --check ..."
"${RUFF_CMD[@]}" format --check .

# Base for "what is being pushed": the remote-tracking commit when the branch
# already exists there, otherwise everything this branch adds to main.
BASE="${LOREGARDEN_TESTS_BASE:-}"
if [ -z "$BASE" ]; then
  BASE="$(git -C "$ROOT" rev-parse --verify --quiet "@{push}" || true)"
fi
if [ -z "$BASE" ]; then
  BASE="$(git -C "$ROOT" rev-parse --verify --quiet origin/main || true)"
fi

TARGETS=""
SELECT_REASON=""
if [ -n "${LOREGARDEN_FULL_TESTS:-}" ]; then
  SELECT_REASON="LOREGARDEN_FULL_TESTS is set"
elif [ -z "$BASE" ]; then
  SELECT_REASON="no @{push} or origin/main to diff against"
else
  ERR_FILE="$(mktemp)"
  set +e
  TARGETS="$(python3 "$ROOT/.lefthook/scripts/select_pytest_targets.py" --repo "$ROOT" --base "$BASE" 2>"$ERR_FILE")"
  SELECT_STATUS=$?
  set -e
  if [ $SELECT_STATUS -ne 0 ]; then
    TARGETS=""
    SELECT_REASON="$(cat "$ERR_FILE")"
  fi
  rm -f "$ERR_FILE"
fi

if [ -z "$TARGETS" ]; then
  echo "pre-push: full pytest run — ${SELECT_REASON:-selection unavailable}"
  echo "pre-push: pytest -q -n auto ..."
  LOREGARDEN_REPO_ROOT="$ROOT" "${RUN[@]}" pytest -q -n auto --basetemp="$BASETEMP"
  exit 0
fi

# Paths arrive repo-relative; pytest runs from server/.
FILES=()
while IFS= read -r target; do
  [ -n "$target" ] || continue
  rel="${target#server/}"
  if [ ! -f "$PY_ROOT/$rel" ]; then
    # A path pytest cannot collect is reported as a pass, not an error, so a
    # stale selection would look exactly like a green suite.
    echo "pre-push: selector named a missing file: $target" >&2
    exit 1
  fi
  FILES+=("$rel")
done <<< "$TARGETS"

echo "pre-push: pytest -q -n auto on ${#FILES[@]} test file(s) reaching the pushed changes:"
printf '  %s\n' "${FILES[@]}"
echo "pre-push: (CI runs the full suite; LOREGARDEN_FULL_TESTS=1 to run it here)"
LOREGARDEN_REPO_ROOT="$ROOT" "${RUN[@]}" pytest -q -n auto --basetemp="$BASETEMP" "${FILES[@]}"
