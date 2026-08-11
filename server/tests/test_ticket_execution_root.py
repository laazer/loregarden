"""Where a stage runs: the ticket's worktree, not the shared checkout.

The shared checkout switching branches under every run is the mechanism behind
both known failure modes — a crash leaves it half-applied, and two tickets
cannot run at once. These tests pin that ticket execution never moves it.
"""

from pathlib import Path

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from loregarden.services.git_automation_config import serialize_override
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session
from tests.worktree_helpers import head_branch, make_repo, make_ticket


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
    return make_ticket(session, workspace)


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


def test_a_stage_runs_in_the_ticket_worktree_and_leaves_the_checkout_alone(
    session, workspace, ticket, repo
):
    run = _run(session, workspace, ticket, "r1")

    root = resolve_execution_root(session, run, ticket, workspace)

    assert root != repo
    assert head_branch(root) == "loregarden/lg-1-add-the-thing"
    # The branch was created by `git worktree add -b`, so the shared checkout
    # never left main.
    assert head_branch(repo) == "main"
    assert run.worktree_id


def test_the_next_stage_of_the_same_ticket_lands_in_the_same_tree(session, workspace, ticket, repo):
    first = resolve_execution_root(
        session, _run(session, workspace, ticket, "r1"), ticket, workspace
    )
    (first / "stage-one.txt").write_text("work\n")

    second = resolve_execution_root(
        session, _run(session, workspace, ticket, "r2"), ticket, workspace
    )

    assert second == first
    assert (second / "stage-one.txt").exists()


def test_a_run_that_already_has_a_worktree_keeps_it(session, workspace, ticket, repo):
    """The parallel queue and stage fan-out assign a worktree before dispatch;
    resolution must not hand those runs the ticket's shared tree instead."""
    run = _run(session, workspace, ticket, "r1")
    service = WorktreeService(session, repo_path=str(repo))
    own = service.create_worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        parent_branch="main",
        branch="attempt-1",
    )
    assert own is not None
    run.worktree_id = own.id
    session.add(run)
    session.commit()

    root = resolve_execution_root(session, run, ticket, workspace)

    assert root == Path(own.worktree_path)


def test_turning_the_worktree_policy_off_keeps_the_shared_checkout(
    session, workspace, ticket, repo
):
    ticket.git_automation_json = serialize_override({"worktree": False})
    session.add(ticket)
    session.commit()
    run = _run(session, workspace, ticket, "r1")

    root = resolve_execution_root(session, run, ticket, workspace)

    assert root == repo
    assert not run.worktree_id


def test_two_tickets_resolve_to_two_trees(session, workspace, ticket, repo):
    other = Ticket(
        external_id="LG-2",
        workspace_id=workspace.id,
        title="Other thing",
        branch="loregarden/lg-2-other-thing",
    )
    session.add(other)
    session.commit()
    session.refresh(other)

    one = resolve_execution_root(session, _run(session, workspace, ticket, "r1"), ticket, workspace)
    two = resolve_execution_root(session, _run(session, workspace, other, "r2"), other, workspace)

    assert one != two
    assert head_branch(repo) == "main"
