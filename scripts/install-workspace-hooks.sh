#!/usr/bin/env bash
# Install loregarden's organization guardrails into a workspace's pre-commit hooks.
#
# The orchestration gates already run these against every workspace during a run
# (agent_context/orchestration/*.yaml). This covers the other half: commits a
# human makes by hand, which no gate ever sees.
#
# The installed entries *reference* loregarden's copy by absolute path rather
# than copying the scripts in. A copy in each repo is a copy that drifts, and
# these rules are meant to be one thing.
#
# Usage:
#   scripts/install-workspace-hooks.sh /path/to/workspace [...]
#   scripts/install-workspace-hooks.sh --all                        # every workspace in the database
#   scripts/install-workspace-hooks.sh --check [--all | /path/to/workspace ...]   # report only
#
# Idempotent: the block is delimited by markers and rewritten in place. It names
# one dispatcher (.lefthook/scripts/workspace-gates.sh), not the gates, so a gate
# added there needs no reinstall; this only has to run again when the block
# itself changes or loregarden's checkout moves.

set -euo pipefail

# The interpreter for the Python halves. The server names its own
# (services/workspace_integration.py), so it is not re-resolved from PATH on
# every call — through a pyenv shim that costs seconds per call, not
# milliseconds. A shell run falls back to python3.
PYTHON="${LOREGARDEN_PYTHON:-python3}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOREGARDEN_ROOT="$ROOT"

# The block bakes an absolute path into another repo's lefthook.yml, so it must
# name a checkout that outlives this run. A linked worktree does not: installing
# from one strands every workspace when the branch merges, and --check from one
# reports every correctly installed workspace as outdated. Render against the
# primary checkout, as install-workspace-docs.sh does.
# shellcheck source=lib/primary-checkout.sh
source "$ROOT/scripts/lib/primary-checkout.sh"
if primary="$(resolve_primary_checkout)" && [ "$primary" != "$ROOT" ]; then
  echo "note: rendering paths against the primary checkout $primary, not this worktree" >&2
  LOREGARDEN_ROOT="$primary"
fi

check_only=0
all=0
targets=()
for arg in "$@"; do
  case "$arg" in
    --check) check_only=1 ;;
    --all) all=1 ;;
    *) targets+=("$arg") ;;
  esac
done

if [ "$all" -eq 1 ]; then
  [ ${#targets[@]} -eq 0 ] || { echo "--all takes no paths" >&2; exit 2; }
  listing="$("$PYTHON" "$ROOT/scripts/list_workspace_roots.py" --loregarden-root "$LOREGARDEN_ROOT")"
  while IFS=$'\t' read -r _slug root; do
    [ -n "$root" ] && targets+=("$root")
  done <<<"$listing"
  [ ${#targets[@]} -gt 0 ] || { echo "no workspaces besides loregarden itself" >&2; exit 0; }
fi

if [ ${#targets[@]} -eq 0 ]; then
  echo "usage: $0 [--check] (--all | <workspace-root> [...])" >&2
  exit 2
fi

# The primary checkout may be on a branch that predates the dispatcher (this
# script running from a worktree ahead of main). Writing a block that names a
# missing script would fail every commit in every workspace.
dispatcher="$LOREGARDEN_ROOT/.lefthook/scripts/workspace-gates.sh"
if [ "$check_only" -eq 0 ] && [ ! -f "$dispatcher" ]; then
  echo "refusing: $dispatcher does not exist — merge the branch that adds it, then run this from $LOREGARDEN_ROOT" >&2
  exit 1
fi

status=0
for target in "${targets[@]}"; do
  if [ ! -e "$target/.git" ]; then  # a file, not a directory, in a linked worktree
    echo "skip: $target is not a git repository" >&2
    status=1
    continue
  fi
  if [ ! -f "$target/lefthook.yml" ]; then
    echo "skip: $target has no lefthook.yml (install lefthook there first)" >&2
    status=1
    continue
  fi
  if ! "$PYTHON" "$ROOT/scripts/install_workspace_hooks.py" \
    --config "$target/lefthook.yml" \
    --loregarden-root "$LOREGARDEN_ROOT" \
    ${check_only:+$([ "$check_only" -eq 1 ] && echo --check)}; then
    status=1
  fi
done

exit "$status"
