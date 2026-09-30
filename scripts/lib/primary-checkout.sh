# shellcheck shell=bash
# Sourced, not run. Defines resolve_primary_checkout, which prints the primary
# checkout — the one holding the live database — for the checkout at $ROOT.
# A linked worktree has no data of its own; its `data/loregarden.db` is an empty
# file nobody meant to read. Fails (returns 1) when there is no live database
# anywhere, e.g. in CI or a cloud session.
resolve_primary_checkout() {
  local common
  common="$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null)" || return 1
  case "$common" in
    /*) ;;
    *) common="$ROOT/$common" ;;
  esac
  local primary
  primary="$(cd "$common/.." 2>/dev/null && pwd)" || return 1
  # Only trust it if it looks like this project and actually holds the database.
  [ -d "$primary/agent_context" ] && [ -d "$primary/server" ] || return 1
  [ -f "$primary/data/loregarden.db" ] || return 1
  printf '%s' "$primary"
}
