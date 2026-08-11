"""One worktree per ticket, reused by every stage of that ticket.

The parallel-queue path cuts a worktree per *run*, which is right for N
competing attempts and wrong for a pipeline: a ticket's twelve stages would
each get their own tree and the later ones would not see the earlier ones'
work. These tests pin the reuse, and pin that reuse never resets the ticket's
branch back to its parent.
"""

import subprocess
from pathlib import Path

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace, WorktreeState
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session
from tests.worktree_helpers import head_branch, make_repo


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


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    ticket = Ticket(
        external_id="LG-1",
        workspace_id=workspace.id,
        title="Add the thing",
        branch="loregarden/lg-1-add-the-thing",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _run(session, workspace, ticket, code):
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_first_stage_creates_the_ticket_worktree_on_the_ticket_branch(
    session, workspace, ticket, repo
):
    service = WorktreeService(session, repo_path=str(repo))

    worktree = service.get_or_create_for_ticket(ticket, _run(session, workspace, ticket, "r1").id)

    assert worktree is not None
    assert worktree.ticket_id == ticket.id
    assert worktree.branch == "loregarden/lg-1-add-the-thing"
    assert Path(worktree.worktree_path).is_dir()
    assert head_branch(worktree.worktree_path) == "loregarden/lg-1-add-the-thing"
    # The shared checkout keeps its own branch — the whole point of isolation.
    assert head_branch(repo) == "main"


def test_a_later_stage_reuses_the_same_worktree_row_and_path(session, workspace, ticket, repo):
    service = WorktreeService(session, repo_path=str(repo))

    first = service.get_or_create_for_ticket(ticket, _run(session, workspace, ticket, "r1").id)
    second = service.get_or_create_for_ticket(ticket, _run(session, workspace, ticket, "r2").id)

    assert first is not None and second is not None
    assert second.id == first.id
    assert second.worktree_path == first.worktree_path


def test_reuse_keeps_the_work_the_earlier_stage_committed(session, workspace, ticket, repo):
    """`worktree add -B` would reset the ticket branch to main and throw the
    earlier stage's commits away. Reuse must not go near the branch."""
    service = WorktreeService(session, repo_path=str(repo))
    first = service.get_or_create_for_ticket(ticket, _run(session, workspace, ticket, "r1").id)
    assert first is not None
    (Path(first.worktree_path) / "stage-one.txt").write_text("work\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "stage one"]):
        subprocess.run(["git", *args], cwd=first.worktree_path, check=True, capture_output=True)

    second = service.get_or_create_for_ticket(ticket, _run(session, workspace, ticket, "r2").id)

    assert second is not None
    assert (Path(second.worktree_path) / "stage-one.txt").exists()


def test_a_worktree_whose_directory_vanished_is_retired_and_replaced(
    session, workspace, ticket, repo
):
    """A removed directory must not be handed back as a working cwd, and the
    branch it still holds in git's admin data must be freed for the new one."""
    service = WorktreeService(session, repo_path=str(repo))
    first = service.get_or_create_for_ticket(ticket, _run(session, workspace, ticket, "r1").id)
    assert first is not None
    subprocess.run(
        ["git", "worktree", "remove", "--force", first.worktree_path],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    second = service.get_or_create_for_ticket(ticket, _run(session, workspace, ticket, "r2").id)

    assert second is not None
    assert second.id != first.id
    assert Path(second.worktree_path).is_dir()
    assert first.state == WorktreeState.CLEANUP
    assert second.branch == first.branch


def test_two_tickets_get_two_worktrees(session, workspace, ticket, repo):
    other = Ticket(
        external_id="LG-2",
        workspace_id=workspace.id,
        title="Other thing",
        branch="loregarden/lg-2-other-thing",
    )
    session.add(other)
    session.commit()
    session.refresh(other)
    service = WorktreeService(session, repo_path=str(repo))

    one = service.get_or_create_for_ticket(ticket, _run(session, workspace, ticket, "r1").id)
    two = service.get_or_create_for_ticket(other, _run(session, workspace, other, "r2").id)

    assert one is not None and two is not None
    assert one.worktree_path != two.worktree_path
    assert head_branch(two.worktree_path) == "loregarden/lg-2-other-thing"
