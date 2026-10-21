"""Is a branch's work on a ref? The one place that answers.

Publishing a tree squashes it onto the base (#621), so afterwards no base
commit is a commit on the branch, and ancestry says "not landed" about work
that is plainly there. Every surface that asked had to learn that on its own,
and six fixes taught six of them (#609, #627, #631, #654, #655, #659) with four
different checks: ancestry, `adds_nothing` (one merge against the tip),
`superseding_commit`, and ancestry inside `merge_tips`. They disagreed. A squash
that the base later edited read as unlanded to `adds_nothing` and as landed to
`superseding_commit`, so the same tree could have its publish card closed and
an empty PR opened for it.

**The answer.** The work is on the ref when some commit in the ref's history
holds everything the branch adds: merging the branch into that commit would
leave it unchanged. That is ancestry when history is shared, and
`superseding_commit` when it is not (a squash, a cherry-pick). It is a fact
about history: a revert on the ref afterwards is the ref's own later change,
not the branch's work failing to land, and a later edit to the same lines is
the case `superseding_commit` exists for (lg-durable-remote-335).

A branch that adds nothing (no commits of its own, or commits that cancel out)
is held trivially, as `land_ticket` has always treated a branch its target
contains: there is nothing to deliver, so nothing is missing.

`test_work_landed` builds each shape of history for real and checks the answer,
and fails any module other than this one that reaches for the building blocks.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.services.git_merge_noco import (
    adds_nothing,
    is_ancestor,
    merge_base,
    superseding_commit,
)


def landing_commit(repo_root: Path, branch_sha: str, ref_sha: str) -> str:
    """A commit on ``ref_sha``'s history holding everything ``branch_sha`` adds.

    "" when there is none. Raises `RuntimeError` when git cannot tell, which is
    not an answer either way: the caller decides whether "unknown" waits,
    blocks or retries, and says so.
    """
    if not branch_sha or not ref_sha:
        return ""
    if branch_sha == ref_sha or is_ancestor(repo_root, branch_sha, ref_sha):
        return branch_sha
    holder = superseding_commit(repo_root, branch_sha, ref_sha)
    if holder:
        return holder
    # `superseding_commit` only tries base commits touching a path the branch
    # changed, so a branch whose commits cancel out has no candidate at all,
    # though it adds nothing. The fork point holds that nothing.
    fork = merge_base(repo_root, branch_sha, ref_sha)
    if fork and adds_nothing(repo_root, fork, branch_sha):
        return fork
    return ""


def work_is_on(repo_root: Path, branch_sha: str, ref_sha: str) -> bool:
    """`landing_commit`, for a caller that needs only yes or no."""
    return bool(landing_commit(repo_root, branch_sha, ref_sha))
