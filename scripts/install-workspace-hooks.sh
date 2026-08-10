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
#   scripts/install-workspace-hooks.sh --check /path/to/workspace   # report only
#
# Idempotent: the block is delimited by markers and rewritten in place.

set -euo pipefail

LOREGARDEN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

check_only=0
targets=()
for arg in "$@"; do
  case "$arg" in
    --check) check_only=1 ;;
    *) targets+=("$arg") ;;
  esac
done

if [ ${#targets[@]} -eq 0 ]; then
  echo "usage: $0 [--check] <workspace-root> [...]" >&2
  exit 2
fi

status=0
for target in "${targets[@]}"; do
  if [ ! -d "$target/.git" ]; then
    echo "skip: $target is not a git repository" >&2
    status=1
    continue
  fi
  if [ ! -f "$target/lefthook.yml" ]; then
    echo "skip: $target has no lefthook.yml (install lefthook there first)" >&2
    status=1
    continue
  fi
  if ! python3 "$LOREGARDEN_ROOT/scripts/install_workspace_hooks.py" \
    --config "$target/lefthook.yml" \
    --loregarden-root "$LOREGARDEN_ROOT" \
    ${check_only:+$([ "$check_only" -eq 1 ] && echo --check)}; then
    status=1
  fi
done

exit "$status"
