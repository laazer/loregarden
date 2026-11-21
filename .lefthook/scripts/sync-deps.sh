#!/usr/bin/env bash
# Re-sync dependencies when a pull, merge, rebase or branch switch changed a lockfile.
#
#   sync-deps.sh merge                       (post-merge)
#   sync-deps.sh checkout <old> <new> <flag>  (post-checkout)
#   sync-deps.sh rewrite <rebase|amend>       (post-rewrite)
#
# A pull moves the source but leaves server/.venv and client/node_modules as
# they were. #932 bumped lore-eden in server/uv.lock; the primary checkout
# pulled it, the dev server reloaded on the old lore-eden, and the worker died
# at import ("lore-eden instance tools and McpTool disagree on: [...]"). That
# took the control plane down until someone ran `uv sync` by hand.
#
# Runs in the foreground so the dependencies are current before anything
# (a reload, a gate, a test) runs against them. A failed install exits
# non-zero, which lefthook prints, and names the command to re-run; the
# git operation itself has already finished and is not undone.
set -uo pipefail

# The revision the tree moved from. Git leaves ORIG_HEAD there for a merge
# (a fast-forward pull included) and for a rebase.
case "${1:-}" in
  merge)
    old=ORIG_HEAD new=HEAD ;;
  checkout)
    # The flag is 0 for `git checkout -- <file>`: no branch moved.
    [ "${4:-}" = 1 ] || exit 0
    # A rebase checks out commits one by one; post-rewrite syncs once at the
    # end. (Only here: git still has the rebase directory during post-rewrite.)
    gitdir="$(git rev-parse --git-dir)"
    if [ -d "$gitdir/rebase-merge" ] || [ -d "$gitdir/rebase-apply" ]; then
      exit 0
    fi
    old="${2:-}" new="${3:-HEAD}" ;;
  rewrite)
    # An amend is the committer's own edit, not someone else's lockfile.
    [ "${2:-}" = rebase ] || exit 0
    old=ORIG_HEAD new=HEAD ;;
  *)
    echo "sync-deps: usage: sync-deps.sh merge | checkout <old> <new> <flag> | rewrite <kind>" >&2
    exit 2 ;;
esac

null_rev=0000000000000000000000000000000000000000

# `git worktree add` and a fresh clone check out from the null revision: there
# is nothing to compare, and the SessionStart bootstrap installs a new worktree.
if [ -z "$old" ] || [ "$old" = "$null_rev" ]; then
  exit 0
fi

if ! changed="$(git diff --name-only "$old" "$new" -- \
  server/uv.lock server/pyproject.toml client/package-lock.json)"; then
  echo "sync-deps: could not compare $old..$new; check dependencies by hand: task setup" >&2
  exit 1
fi
[ -n "$changed" ] || exit 0

root="$(git rev-parse --show-toplevel)"
status=0

if printf '%s\n' "$changed" | grep -q '^server/'; then
  echo "sync-deps: server/uv.lock or pyproject.toml changed; running uv sync"
  if ! command -v uv >/dev/null 2>&1; then
    echo "sync-deps: uv not on PATH; run: cd server && uv sync" >&2
    status=1
  elif ! (cd "$root/server" && uv sync --extra dev); then
    echo "sync-deps: uv sync FAILED; run: cd server && uv sync" >&2
    status=1
  fi
fi

if printf '%s\n' "$changed" | grep -q '^client/'; then
  echo "sync-deps: client/package-lock.json changed; running npm ci"
  # shellcheck source=ensure-node.sh
  if ! (source "$root/.lefthook/scripts/ensure-node.sh" && cd "$root/client" \
    && npm ci --no-audit --no-fund); then
    echo "sync-deps: npm ci FAILED; run: cd client && npm ci" >&2
    status=1
  fi
fi

exit "$status"
