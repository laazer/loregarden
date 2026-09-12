#!/usr/bin/env bash
# Teach a workspace's AGENTS.md about loregarden's control plane.
#
# The sibling of install-workspace-hooks.sh, and the half of workspace setup that
# was missing: hooks carry the rules into the workspace's commits, this carries
# the *tools* into the workspace's agents. Without it an agent working in that
# repo reads an AGENTS.md that never mentions the database its ticket lives in,
# and goes looking for a ticket file that does not exist.
#
# Usage:
#   scripts/install-workspace-docs.sh /path/to/workspace [...]
#   scripts/install-workspace-docs.sh --slug blobert /path/to/workspace
#   scripts/install-workspace-docs.sh --check /path/to/workspace   # report only
#
# --slug fills the workspace_slug in the rendered CLI examples; without it the
# block carries a placeholder rather than a guess. Only valid for a single target.
#
# Idempotent: the section is delimited by markers and rewritten in place. AGENTS.md
# is created if the workspace has none.

set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOREGARDEN_ROOT="$SCRIPT_ROOT"

# The block bakes absolute paths into another repo's AGENTS.md, so they must point
# at a checkout that outlives this run. A linked worktree does not: installing from
# one leaves every workspace referencing a directory that disappears when the branch
# merges, while --check keeps reporting it current. Resolve to the primary checkout.
resolve_primary_checkout() {
  local common primary
  common="$(git -C "$SCRIPT_ROOT" rev-parse --git-common-dir 2>/dev/null)" || return 1
  case "$common" in
    /*) ;;
    *) common="$SCRIPT_ROOT/$common" ;;
  esac
  primary="$(cd "$common/.." 2>/dev/null && pwd)" || return 1
  [ -d "$primary/agent_context" ] && [ -d "$primary/scripts" ] || return 1
  printf '%s' "$primary"
}

# The code runs from this checkout; only the rendered paths move.
if primary="$(resolve_primary_checkout)" && [ "$primary" != "$SCRIPT_ROOT" ]; then
  echo "note: rendering paths against the primary checkout $primary, not this worktree" >&2
  LOREGARDEN_ROOT="$primary"
fi

check_only=0
slug=""
targets=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check) check_only=1 ;;
    --slug)
      shift
      [ $# -gt 0 ] || { echo "--slug needs a value" >&2; exit 2; }
      slug="$1"
      ;;
    --slug=*) slug="${1#--slug=}" ;;
    *) targets+=("$1") ;;
  esac
  shift
done

if [ ${#targets[@]} -eq 0 ]; then
  echo "usage: $0 [--check] [--slug <slug>] <workspace-root> [...]" >&2
  exit 2
fi
if [ -n "$slug" ] && [ ${#targets[@]} -gt 1 ]; then
  echo "--slug names one workspace; pass one target or drop it" >&2
  exit 2
fi

status=0
for target in "${targets[@]}"; do
  if [ ! -d "$target/.git" ]; then
    echo "skip: $target is not a git repository" >&2
    status=1
    continue
  fi
  args=(--agents-file "$target/AGENTS.md" --loregarden-root "$LOREGARDEN_ROOT")
  [ -n "$slug" ] && args+=(--workspace-slug "$slug")
  [ "$check_only" -eq 1 ] && args+=(--check)
  if ! python3 "$SCRIPT_ROOT/scripts/install_workspace_agents_doc.py" "${args[@]}"; then
    status=1
  fi
done

exit "$status"
