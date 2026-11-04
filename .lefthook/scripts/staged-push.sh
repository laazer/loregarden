#!/usr/bin/env bash
# lefthook `skip` condition for the pre-push test suites: exits 0 — skip the
# suite — only for a merge-queue staging push, and 1 for every other push.
#
#   bash .lefthook/scripts/staged-push.sh <suite name>
#
# A staging push is one commit cut directly on the remote base's tip
# (`git_merge_noco.squash_onto`), pushed as `publish/<branch>` by the control
# plane (`git_automation.push_commit_as`), which names the commit in
# LOREGARDEN_PUSH_STAGED. The suites would test the wrong tree for it: they run
# in the checkout the push fired from — the primary checkout, on whatever it
# holds — not on the pushed commit. And the commit cannot reach the base without
# its pull request's required checks passing on exactly that commit: the merge
# queue (lg-milestone-that-967) refuses to land a tree other than the one that
# went green. So the run here proves nothing about the work and costs a heavy
# capacity lease for up to 37 minutes; CI on the staged commit is the one test
# of the merged tree. It runs as a `skip` condition, before `capacity-run.sh`,
# so a staging push does not wait in line for capacity it will not use.
#
# Checked, not a flag anyone can set: the named commit must exist and its only
# parent must be the fetched remote base's tip — the shape every staging cut has
# and an ordinary branch push does not. The signed-commits gate still runs.
set -uo pipefail

suite="${1:-pre-push suite}"
sha="${LOREGARDEN_PUSH_STAGED:-}"
base="${LOREGARDEN_PUSH_STAGED_BASE:-main}"
[ -n "$sha" ] || exit 1

root="$(cd "$(dirname "$0")/../.." && pwd)"
if ! parents="$(git -C "$root" rev-list --parents -n 1 "$sha" 2>/dev/null)"; then
  echo "pre-push: LOREGARDEN_PUSH_STAGED names $sha, not a commit here; running $suite" >&2
  exit 1
fi
tip="$(git -C "$root" rev-parse --verify --quiet "refs/remotes/origin/$base" || true)"
read -r -a ids <<< "$parents"
if [ "${#ids[@]}" -ne 2 ] || [ -z "$tip" ] || [ "${ids[1]}" != "$tip" ]; then
  echo "pre-push: $sha is not one commit on origin/$base's tip; running $suite" >&2
  exit 1
fi
echo "pre-push: $suite skipped: merge-queue staging push of ${sha:0:12} on origin/$base."
echo "pre-push: its pull request's required CI tests exactly this commit before it can land."
exit 0
