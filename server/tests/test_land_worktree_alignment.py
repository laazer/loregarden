"""What the two-tree refresh does to a worktree it cannot align (lg-milestone-that-1419).

R2's adversarial half. `tests/test_land_ticket.py` is `lg-milestone-that-920`'s
own oracle and AC3 forbids editing it, so the cases that are not in it live
here.

The repair is `read-tree -m -u <before> <after>` in place of `read-tree -m -u
HEAD`, and the two forms differ in more than the dropped staged addition that
AC2 names. Measured in a scratch repository at `58b4d0f4`, with the worktree
holding a local edit to a file the refresh also changed:

| form | rc | what happened |
|---|---|---|
| `read-tree -m -u HEAD` (one tree) | 0 | the index reset to the new tip, so the operator's edit became a *revert* of the incoming change, and the orchestrator's sweep would commit it |
| `read-tree -m -u <before> <after>` | 128 | `error: Entry 'seed.txt' not uptodate. Cannot merge.` — nothing written, the edit untouched |

So the fix turns a land that silently reverted another ticket's landed change
into a refused land that says which worktree is in the way, which is the same
answer the untracked-collision leg already gives. That is the behaviour pinned
here, because the cheapest way to make the newly-refusing case "pass" is a
fallback to the one-tree form or a `--reset`, and either one re-opens
`lg-milestone-that-920` while every landed test stays green.
"""

from __future__ import annotations

import pytest
from loregarden.services.land_ticket import land_ticket
from sqlmodel import Session
from tests import land_receipt_helpers as helpers
from tests.worktree_helpers import commit_on, git


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _needs_a_refresh(session, tmp_path, name):
    """A landing whose target moved after the branch was cut, touching `seed.txt`.

    The move is what makes `_refresh_branch` write a commit and reach
    `_align_worktrees` at all; a target that did not move is the
    `already_contained` leg, which aligns nothing and would let every assertion
    below pass against the broken code.
    """
    landing = helpers.build(session, tmp_path, name)
    commit_on(landing.repo, landing.target, "seed.txt", "seed, revised on the target\n")
    return landing


def _worktree_on(repo, branch, path):
    git(repo, "worktree", "add", "-q", str(path), branch)
    return path


def test_a_locally_modified_file_in_the_refreshs_way_blocks_the_landing(session, tmp_path):
    """git refuses rather than writing over the edit, and the refusal is reported.

    The operator's uncommitted edit is to the very file the refresh brings in a
    new version of. There is no answer that both applies the ref move and keeps
    the edit, so the land is refused with git's own words — the same shape as
    the untracked-collision leg AC2 requires to keep working. What must NOT
    happen is the pre-fix behaviour: rc=0, the index silently reset to the new
    tip, and the edit left standing as a revert of the incoming change for the
    orchestrator's sweep to commit.
    """
    landing = _needs_a_refresh(session, tmp_path, "collide")
    tree = _worktree_on(landing.repo, landing.ticket.branch, tmp_path / "wt-collide")
    (tree / "seed.txt").write_text("the operator's edit\n")
    target_before = helpers.sha(landing.repo, landing.target)

    result = land_ticket(session, landing.ticket, landing.workspace)
    session.refresh(landing.ticket)

    assert not result.ok, (
        "the refresh could not align the worktree and the land went ahead anyway; "
        "a one-tree read-tree resets the index to the new tip, which turns the "
        "operator's edit into a revert of the change that just landed"
    )
    assert str(tree) in result.detail, "which worktree is in the way"
    assert "seed.txt" in result.detail, "which path"
    assert "uptodate" in result.detail.lower(), "git's own words, not a paraphrase"
    assert (tree / "seed.txt").read_text() == "the operator's edit\n", (
        "the edit was discarded — this is lg-milestone-that-920's failure on another path"
    )
    assert helpers.sha(landing.repo, landing.target) == target_before, "nothing landed"
    assert landing.ticket.landed_sha == "", "a refused land records nothing"


def test_a_worktree_whose_directory_is_gone_does_not_refuse_the_landing(session, tmp_path):
    """git keeps listing a worktree whose directory was deleted, with its branch.

    `_align_worktrees` skips it on the `prunable` attribute, because running
    `read-tree` in a directory that is not there raises before git is reached.
    Moving the before-tree read into `_refresh_branch` changes that function's
    signature, and this is the leg a rewrite drops: the land would then block on
    a worktree nobody has, on every pass.
    """
    landing = _needs_a_refresh(session, tmp_path, "pruned")
    tree = _worktree_on(landing.repo, landing.ticket.branch, tmp_path / "wt-pruned")
    for child in sorted(tree.rglob("*"), reverse=True):
        child.unlink() if child.is_file() or child.is_symlink() else child.rmdir()
    tree.rmdir()
    assert "prunable" in git(landing.repo, "worktree", "list", "--porcelain").stdout, (
        "git no longer reports the deleted worktree as prunable, so this asserts nothing"
    )

    result = land_ticket(session, landing.ticket, landing.workspace)

    assert result.ok, result.detail
    assert result.landed_sha, "the landing was refused by a worktree that is not there"


def test_the_refresh_still_carries_the_incoming_change_into_the_worktree(session, tmp_path):
    """The other direction, and the reason `<before>` may not be recomputed.

    `read-tree -m -u <after> <after>` is a legal two-tree merge between
    identical trees: it refuses nothing, writes nothing, and leaves the worktree
    exactly as stale as doing nothing at all. So a repair that reads its
    before-tree AFTER `merge_without_checkout` examines nothing while returning
    "" — rc=0, no refusal, and every worktree assertion that only checks for
    survival still passes. What catches it is the incoming file having to
    actually arrive.
    """
    landing = _needs_a_refresh(session, tmp_path, "arrive")
    commit_on(landing.repo, landing.target, "sibling.txt", "another ticket\n")
    tree = _worktree_on(landing.repo, landing.ticket.branch, tmp_path / "wt-arrive")

    result = land_ticket(session, landing.ticket, landing.workspace)

    assert result.ok, result.detail
    assert (tree / "sibling.txt").exists(), (
        "the refresh's commit never reached the worktree, so the before-tree the "
        "repair compared against was the after-tree"
    )
    assert (tree / "sibling.txt").read_text() == "another ticket\n"
    assert (tree / "seed.txt").read_text() == "seed, revised on the target\n"
    assert git(tree, "status", "--porcelain").stdout.strip() == "", (
        "the worktree is left clean, not staged-dirty in reverse"
    )
