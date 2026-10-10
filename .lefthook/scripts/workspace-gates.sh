#!/usr/bin/env bash
# Every workspace-agnostic loregarden gate, behind one command.
#
# This is the only list of them. Other workspaces' pre-commit (the managed block
# scripts/install-workspace-hooks.sh writes) and every orchestration profile call
# this script rather than naming each gate, so adding, removing or re-flagging a
# gate is an edit here — no reinstall in any workspace, no profile to update.
#
# Usage:
#   workspace-gates.sh <file>...                          # pre-commit: lefthook's {staged_files}, cwd = repo
#   workspace-gates.sh --repo <root> --scope worktree     # orchestration transition gate
#   workspace-gates.sh --list                             # print the gates, one per line
#
# --repo/--scope/--base pass through to every gate unchanged. Given files, each
# gate sees only its own language's and is skipped when there are none — what
# lefthook's per-command globs used to do.
#
# Every gate runs even after one fails, so one commit reports everything. Exit:
#   1   any gate failed
#   69  none failed, but at least one could not run (EX_UNAVAILABLE — the
#       orchestration runner routes that to a human, not to the stage's agent)
#   0   all passed
# A real failure outranks an unavailable gate: its findings are fixable now, and
# the unavailable one still reports itself on the next run.
set -uo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EX_UNAVAILABLE=69

#: "<language> <checker>". Python runs through server_python.sh: the checkers need
#: >=3.11 and a bare `python3` resolves against the *target* workspace's PATH.
GATES=(
  "py py_organization_check.py"
  "py py_silent_except_check.py"
  "ts ts_organization_check.cjs"
  "ts ts_no_silent_failures_check.cjs"
  "ts ts_ux_states_check.cjs"
)

usage() {
  echo "usage: $0 (<file>... | --repo <root> --scope <scope> [--base <ref>] | --list)" >&2
  exit 2
}

flags=()
files=()
while [ $# -gt 0 ]; do
  case "$1" in
    --list)
      for gate in "${GATES[@]}"; do echo "${gate#* }"; done
      exit 0
      ;;
    --repo | --scope | --base)
      [ $# -ge 2 ] || usage
      flags+=("$1" "$2")
      shift
      ;;
    -*) usage ;;
    *) files+=("$1") ;;
  esac
  shift
done
[ ${#flags[@]} -gt 0 ] || [ ${#files[@]} -gt 0 ] || usage

failed=0
unavailable=0
for gate in "${GATES[@]}"; do
  lang="${gate%% *}"
  checker="${gate#* }"

  selected=()
  for file in ${files[@]+"${files[@]}"}; do
    case "$lang:$file" in
      py:*.py | ts:*.ts | ts:*.tsx) selected+=("$file") ;;
    esac
  done
  # Handed files, none of them this gate's: nothing for it to examine.
  if [ ${#files[@]} -gt 0 ] && [ ${#selected[@]} -eq 0 ]; then
    continue
  fi

  case "$lang" in
    py) cmd=(bash "$SCRIPTS/server_python.sh" "$SCRIPTS/$checker") ;;
    ts) cmd=(node "$SCRIPTS/$checker") ;;
  esac

  # Merged into stdout: the orchestration runner reports stderr *instead of*
  # stdout when both are present, which would hide one gate's findings behind
  # another gate's warning.
  output="$("${cmd[@]}" ${flags[@]+"${flags[@]}"} ${selected[@]+"${selected[@]}"} 2>&1)"
  rc=$?

  if [ "$rc" -eq 0 ]; then
    verdict="passed"
  elif [ "$rc" -eq "$EX_UNAVAILABLE" ]; then
    verdict="could not run (exit $rc)"
    unavailable=1
  else
    verdict="failed (exit $rc)"
    failed=1
  fi
  if [ "$rc" -ne 0 ] || [ -n "$output" ]; then
    echo "[loregarden gates] $checker — $verdict"
    [ -z "$output" ] || printf '%s\n' "$output"
  fi
done

if [ "$failed" -eq 1 ]; then exit 1; fi
if [ "$unavailable" -eq 1 ]; then exit "$EX_UNAVAILABLE"; fi
exit 0
