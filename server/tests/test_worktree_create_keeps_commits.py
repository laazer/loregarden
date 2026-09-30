"""`create_worktree` on a branch that already has commits (lg-milestone-that-772)."""

from __future__ import annotations

import pytest
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo, make_run


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def test_a_retried_run_gets_the_branch_as_it_is(session, workspace, repo):
    """`worktree add -B` reset the branch to its parent, so the retry's tree
    was a first attempt with the earlier round's commits orphaned."""
    ticket = Ticket(
        external_id="LG-772", workspace_id=workspace.id, title="Retry", branch="loregarden/lg-772"
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    service = WorktreeService(session, repo_path=str(repo))

    first = service.create_worktree(
        workspace_id=workspace.id,
        agent_run_id=make_run(session, workspace, ticket, "r1").id,
        parent_branch="main",
        branch=ticket.branch,
    )
    assert first is not None
    (repo.parent / first.worktree_path).joinpath("work.txt").write_text("first round\n")
    git(first.worktree_path, "add", "-A")
    git(first.worktree_path, "commit", "-q", "-m", "first round")
    first_round = git(first.worktree_path, "rev-parse", "HEAD").stdout.strip()
    # The first tree is gone (crash, cleanup); only the branch survives.
    git(repo, "worktree", "remove", "--force", first.worktree_path)

    second = service.create_worktree(
        workspace_id=workspace.id,
        agent_run_id=make_run(session, workspace, ticket, "r2").id,
        parent_branch="main",
        branch=ticket.branch,
    )

    assert second is not None
    assert git(second.worktree_path, "rev-parse", "HEAD").stdout.strip() == first_round
    assert second.merge_base == first_round
