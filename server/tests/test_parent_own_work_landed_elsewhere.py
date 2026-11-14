"""A parent whose own work reached its target only as `origin` has it counts as landed.

lg-improved-memory-662 and lg-initiatives-cross-732 were squash-merged into
targets that GitHub merged and the local checkout pruned: the targets existed
only as `refs/remotes/origin/…`. Reading `refs/heads` alone, the closing check
held both open (and the invariant reported both) for work that had landed.
"""

from __future__ import annotations

from loregarden.models.domain import TicketState
from loregarden.services.parent_own_work import unlanded_own_work
from loregarden.services.ticket_rollup import reconcile_parent
from tests.test_parent_own_work import (  # noqa: F401 - fixtures are used by name
    family_fixture,
    repo_fixture,
    session_fixture,
)
from tests.worktree_helpers import git


def _squash_onto(repo, branch: str, target: str) -> None:
    tree = repo.parent / "tmp-squash"
    git(repo, "worktree", "add", "-q", str(tree), target)
    git(tree, "merge", "-q", "--squash", branch)
    git(tree, "commit", "-q", "-m", "squash")
    git(repo, "worktree", "remove", "--force", str(tree))


def _only_on_origin(repo, ref: str) -> None:
    tip = git(repo, "rev-parse", f"refs/heads/{ref}").stdout.strip()
    git(repo, "update-ref", f"refs/remotes/origin/{ref}", tip)
    git(repo, "update-ref", "-d", f"refs/heads/{ref}")


def test_a_squash_landing_on_a_target_only_origin_has_closes_the_parent(session, repo, family):
    source, _, target = family
    _squash_onto(repo, source.branch, target)
    _only_on_origin(repo, target)

    assert unlanded_own_work(session, source) == ""
    assert reconcile_parent(session, source) is True
    assert source.state is TicketState.DONE


def test_work_on_no_ref_at_all_still_holds_the_parent_open(session, repo, family):
    """The control: moving the target to origin alone lands nothing."""
    source, _, target = family
    _only_on_origin(repo, target)

    assert source.branch in unlanded_own_work(session, source)
