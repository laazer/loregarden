#!/usr/bin/env bash
# Map a staged file path (repo-root-relative, as lefthook passes it) to a
# Ruff/Pylint argument relative to server/. Prints the relative path on
# stdout when in scope; prints nothing when out of scope (e.g. root-level
# scripts/ or tests/ that CI doesn't lint).
set -euo pipefail

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
      ;;
  esac
}
