"""A ticket's branch is cut from — and refreshed onto — its target (lg-milestone-that-769).

Both dispatch paths used to pick a base by accident: the shared checkout cut
from wherever HEAD was, the worktree path from main. Neither ever contained a
sibling's landed work.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.git_branch import ensure_ticket_branch
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_service import WorktreeRefreshError, WorktreeService
from sqlmodel import Session
from tests.worktree_helpers import commit_on, git, make_repo, make_run


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="cut-from-target", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="milestone")
def milestone_fixture(session, workspace):
    ms = Ticket(
        external_id="lg-ms-769",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


def _child(session, workspace, milestone, external_id):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=external_id,
        branch=f"loregarden/{external_id}",
        parent_ticket_id=milestone.id,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _sha(cwd, ref="HEAD"):
    return git(cwd, "rev-parse", ref).stdout.strip()


def _ancestor(cwd, ancestor, descendant) -> bool:
    import subprocess

    from loregarden.services.git_subprocess import scrubbed_git_env

    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=cwd,
            env=scrubbed_git_env(),
        ).returncode
        == 0
    )


def test_a_worktree_is_cut_from_the_integration_branch_not_main(
    session, workspace, repo, milestone
):
    child = _child(session, workspace, milestone, "lg-a-1")
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    landed = commit_on(repo, target, "sibling.txt", "landed by a sibling\n")

    run = make_run(session, workspace, child, "r1")
    root = resolve_execution_root(session, run, child, workspace)

    assert root != repo
    assert _sha(root) == landed, "cut from the integration branch's tip"
    assert (root / "sibling.txt").exists(), "which main does not have"
    assert not (repo / "sibling.txt").exists()


def test_head_position_in_the_primary_checkout_does_not_matter(session, workspace, repo, milestone):
    child = _child(session, workspace, milestone, "lg-a-2")
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    target_tip = _sha(repo, target)
    git(repo, "checkout", "-q", "-b", "somewhere-else")
    (repo / "noise.txt").write_text("noise\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "noise")

    root = resolve_execution_root(
        session, make_run(session, workspace, child, "r1"), child, workspace
    )

    assert git(repo, "merge-base", target_tip, _sha(root)).stdout.strip() == target_tip
    assert not (root / "noise.txt").exists()


def test_a_prerequisite_landed_after_the_branch_was_cut_still_reaches_the_ticket(
    session, workspace, repo, milestone
):
    """493's case: the base moved between scheduling and the first stage."""
    child = _child(session, workspace, milestone, "lg-a-3")
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    # The ticket branch exists already, cut from the target before anything landed.
    git(repo, "branch", child.branch, target)
    own = commit_on(repo, child.branch, "own.txt", "the ticket's own earlier work\n")
    landed = commit_on(repo, target, "sibling.txt", "landed later\n")

    root = resolve_execution_root(
        session, make_run(session, workspace, child, "r1"), child, workspace
    )

    assert (root / "sibling.txt").exists(), "the landed prerequisite is in the tree"
    assert (root / "own.txt").exists(), "and the ticket's own commits survived"
    assert _ancestor(root, landed, "HEAD") and _ancestor(root, own, "HEAD")


def test_a_conflicting_refresh_fails_loudly_and_leaves_no_half_cut_tree(
    session, workspace, repo, milestone
):
    child = _child(session, workspace, milestone, "lg-a-4")
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    git(repo, "branch", child.branch, target)
    commit_on(repo, child.branch, "shared.txt", "the ticket's version\n")
    commit_on(repo, target, "shared.txt", "a sibling's version\n")
    service = WorktreeService(session, repo_path=str(repo))

    with pytest.raises(WorktreeRefreshError, match="could not take"):
        service.get_or_create_for_ticket(
            child, make_run(session, workspace, child, "r1").id, parent_branch=target
        )

    listed = git(repo, "worktree", "list", "--porcelain").stdout
    assert child.branch not in listed, "the conflicted tree was removed"
    assert service.active_worktree_for_ticket(child.id) is None


def test_the_shared_checkout_cuts_from_and_refreshes_onto_the_start_point(repo, tmp_path):
    ticket = Ticket(
        external_id="lg-a-5",
        branch="loregarden/lg-a-5",
        work_item_type=WorkItemType.TASK,
        title="shared",
        workspace_id="w",
    )
    git(repo, "branch", "integration/x", "main")
    git(repo, "checkout", "-q", "-b", "somewhere-else")

    ensure_ticket_branch(repo, ticket, start_point="integration/x")
    assert _sha(repo) == _sha(repo, "integration/x")

    (repo / "own.txt").write_text("own\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "own")
    landed = commit_on(repo, "integration/x", "sibling.txt", "landed\n")
    git(repo, "checkout", "-q", "main")

    ensure_ticket_branch(repo, ticket, start_point="integration/x")

    assert git(repo, "branch", "--show-current").stdout.strip() == ticket.branch
    assert (repo / "sibling.txt").exists() and (repo / "own.txt").exists()
    assert _ancestor(repo, landed, "HEAD")


def test_the_shared_checkout_does_not_refresh_over_uncommitted_work(repo, caplog):
    ticket = Ticket(
        external_id="lg-a-6",
        branch="loregarden/lg-a-6",
        work_item_type=WorkItemType.TASK,
        title="dirty",
        workspace_id="w",
    )
    git(repo, "branch", "integration/y", "main")
    ensure_ticket_branch(repo, ticket, start_point="integration/y")
    commit_on(repo, "integration/y", "sibling.txt", "landed\n")
    (repo / "seed.txt").write_text("edited but not committed\n")

    with caplog.at_level("WARNING"):
        ensure_ticket_branch(repo, ticket, start_point="integration/y")

    assert not (repo / "sibling.txt").exists()
    assert (repo / "seed.txt").read_text() == "edited but not committed\n"
    assert any("uncommitted" in r.getMessage() for r in caplog.records)


def test_a_conflicting_refresh_in_the_shared_checkout_is_a_valueerror(repo):
    ticket = Ticket(
        external_id="lg-a-7",
        branch="loregarden/lg-a-7",
        work_item_type=WorkItemType.TASK,
        title="conflict",
        workspace_id="w",
    )
    git(repo, "branch", "integration/z", "main")
    ensure_ticket_branch(repo, ticket, start_point="integration/z")
    (repo / "shared.txt").write_text("ticket\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ticket side")
    commit_on(repo, "integration/z", "shared.txt", "sibling\n")
    git(repo, "checkout", "-q", "main")

    with pytest.raises(ValueError, match="could not take"):
        ensure_ticket_branch(repo, ticket, start_point="integration/z")

    assert git(repo, "status", "--porcelain").stdout.strip() == "", "merge was aborted cleanly"


def test_a_repository_with_no_commits_cuts_from_head_and_says_so(tmp_path, caplog):
    """A fresh `git init` has no base branch. The stage must still start."""
    repo = tmp_path / "empty"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    ticket = Ticket(
        external_id="lg-a-8",
        branch="loregarden/lg-a-8",
        work_item_type=WorkItemType.TASK,
        title="empty",
        workspace_id="w",
    )

    with caplog.at_level("WARNING"):
        ensure_ticket_branch(repo, ticket, start_point="main")

    assert git(repo, "branch", "--show-current").stdout.strip() == ticket.branch
    assert any("does not exist" in r.getMessage() for r in caplog.records)
