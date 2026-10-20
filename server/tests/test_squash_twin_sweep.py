"""The invariant `ticket_branches_hold_their_targets_history` on a real git tree.

lg-external-harness-1318: a ticket branch that merged a publish squash of its
integration branch conflicts on every later checkout merge. The checkout
repairs it (`branch_takes_target`); this is the reconcile timer's pass over
branches no stage is about to check out.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.models.domain import WorkItemType, Workspace
from loregarden.services.invariant_registry import TICKET_BRANCHES_HOLD_HISTORY
from loregarden.services.invariants import run_invariant
from loregarden.services.squash_twin_sweep import twinned_branches
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session
from tests.worktree_helpers import commit_on, git, make_repo, make_run, make_tree_ticket


@pytest.fixture(name="session")
def session_fixture(client, isolated_db):
    """`client` seeds the workflow templates the re-entry's escalation files a bug with."""
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="twin", name="twin", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _sha(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


@pytest.fixture(name="twinned")
def twinned_fixture(session, workspace, repo):
    """1026's shape: the child's branch merged a squash of the tree's branch,
    then edited a file the tree added; the tree has moved on since."""
    ms = make_tree_ticket(session, workspace, "lg-ms-1", kind=WorkItemType.MILESTONE)
    child = make_tree_ticket(session, workspace, "lg-child-2", parent=ms)
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    landed = commit_on(repo, target, "shared.txt", "the tree's version\n")
    tree = _sha(repo, f"{landed}^{{tree}}")
    squash = git(repo, "commit-tree", tree, "-p", "main", "-m", "publish").stdout.strip()
    git(repo, "branch", child.branch, squash)
    tip = commit_on(repo, child.branch, "shared.txt", "the tree's version\nthe child's line\n")
    commit_on(repo, target, "later.txt", "landed after the squash\n")
    return child, target, tip, landed


def test_a_twinned_branch_is_found_and_given_its_targets_history(session, repo, twinned):
    child, target, tip, landed = twinned
    [found] = twinned_branches(session)
    assert (found.ticket.id, found.twin.on_target) == (child.id, landed)

    check = run_invariant(session, TICKET_BRANCHES_HOLD_HISTORY)

    assert (check.found, check.reentered, check.repaired) == (1, 1, 1), "after #654, a defect"
    assert _sha(repo, f"{child.branch}^{{tree}}") == _sha(repo, f"{tip}^{{tree}}"), "no file moved"
    git(repo, "merge-base", "--is-ancestor", landed, child.branch)
    merged = git(repo, "merge-tree", "--write-tree", "--name-only", child.branch, target)
    assert merged.returncode == 0, "the branch now takes its target cleanly"
    assert twinned_branches(session) == [], "one pass leaves nothing for the next"


def test_a_branch_with_a_live_run_is_left_to_its_own_checkout(session, workspace, repo, twinned):
    child, _target, tip, _landed = twinned
    make_run(session, workspace, child, "r-live")

    assert twinned_branches(session) == []
    assert _sha(repo, child.branch) == tip


def test_a_workspace_with_no_checkout_is_skipped_not_raised(session, workspace, twinned):
    workspace.repo_path = str(Path(workspace.repo_path) / "moved-away")
    session.add(workspace)
    session.commit()

    assert twinned_branches(session) == []
