#!/usr/bin/env bash
# Map a staged file path (repo-root-relative, as lefthook passes it) to a
# Ruff/Pylint argument relative to server/. Prints the relative path on
# stdout when in scope; prints nothing when out of scope (e.g. root-level
# scripts/ or tests/ that CI doesn't lint).
set -euo pipefail

# Python outside server/ that the gates still own: stdlib-only scripts run by
# the system `python3` (3.9 on this machine), not by the server venv — Claude
# Code hooks, the gate scripts themselves, and CI's helper scripts. Every gate
# skipped them because this mapping printed nothing for them, so a hook landed
# with lint findings and every gate said "(skip) no files for inspection".
#
# The lefthook globs in lefthook.yml list the same directories, and CI lints
# them through py-scripts-ruff.sh. Ruff grades them with server/pyproject.toml
# at PY_SCRIPT_TARGET, so no rule asks for syntax 3.9 cannot run.
PY_SCRIPT_DIRS=(.claude/hooks .lefthook/scripts .github/scripts)
PY_SCRIPT_TARGET=py39

# 0 when `$1` (repo-relative) is a Python file in one of PY_SCRIPT_DIRS.
py_is_script_path() {
  local f="$1" dir
  [[ "$f" == *.py ]] || return 1
  for dir in "${PY_SCRIPT_DIRS[@]}"; do
    [[ "$f" == "$dir"/* ]] && return 0
  done
  return 1
}

py_staged_server_rel() {
  local f="$1"
  local py_root="$2"

  case "$f" in
    "$py_root"/*)
      printf '%s\n' "${f#"$py_root"/}"
      ;;
    server/*)
      printf '%s\n' "${f#server/}"
      ;;
    *)
      # Callers run their tool from server/, so a script outside it is
      # reached as `../<path>`.
      if py_is_script_path "$f"; then
        printf '../%s\n' "$f"
      fi
      ;;
  esac
}
