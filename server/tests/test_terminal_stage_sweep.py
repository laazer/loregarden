"""A ticket parked on its terminal stage is finished by the sweep (801).

The 754 shape: the landing conflicted, the terminal stage blocked, the
orchestration finished BLOCKED. Someone resolved the conflict and cleared the
block, which returns the terminal stage to PENDING — a state only an
orchestration loop consumes, and there is no loop left. Nothing re-entered,
and the ticket read `in_progress` with no blocking text for three hours.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.terminal_stage_sweep import finish_parked_terminal_tickets
from sqlmodel import Session
from tests.worktree_helpers import at_terminal_stage, blocking_text, commit_on, git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="parked", name="parked", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="milestone")
def milestone_fixture(session, workspace):
    ms = Ticket(
        external_id="lg-ms-801",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


@pytest.fixture(name="parked")
def parked_fixture(session, workspace, repo, milestone):
    """A ticket with work on its branch, parked on its terminal stage.

    Built in a fixture, not a test body: a repro whose setup breaks must be an
    ERROR, not a passing assertion about nothing.
    """
    ticket = Ticket(
        external_id="lg-parked-1",
        workspace_id=workspace.id,
        title="Parked on done",
        branch="loregarden/lg-parked-1",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "work.txt", "work\n")
    at_terminal_stage(session, ticket)
    return ticket


def _target(session, ticket, workspace, repo):
    return resolve_target_branch(session, ticket, workspace, repo_root=repo)


def test_a_ticket_parked_on_its_terminal_stage_is_landed_and_finished(
    session, workspace, repo, parked
):
    """754, after the conflict was resolved: nothing was left to finish it."""
    assert parked.state == TicketState.IN_PROGRESS
    assert parked.workflow_stage_key == "done"
    assert parked.workflow_stage_status == StageStatus.PENDING

    finished = finish_parked_terminal_tickets(session)

    assert [t.id for t in finished] == [parked.id]
    session.refresh(parked)
    assert parked.state == TicketState.DONE
    assert parked.landed_sha, "the work reached its target"
    assert parked.landed_branch == _target(session, parked, workspace, repo)


def test_a_landing_that_still_conflicts_blocks_with_its_reason_and_is_not_retried(
    session, workspace, repo, parked
):
    """The sweep must not paper over a conflict, and must reach a fixpoint:
    a BLOCKED terminal stage is no longer PENDING, so the next sweep skips it."""
    target = _target(session, parked, workspace, repo)
    commit_on(repo, parked.branch, "shared.txt", "ticket\n")
    commit_on(repo, target, "shared.txt", "sibling\n")

    finish_parked_terminal_tickets(session)

    session.refresh(parked)
    assert parked.state == TicketState.BLOCKED
    assert parked.workflow_stage_status == StageStatus.BLOCKED
    assert "shared.txt" in blocking_text(session, parked)
    assert parked.landed_sha == ""

    assert finish_parked_terminal_tickets(session) == [], "a blocked stage is not swept again"


def test_a_ticket_with_a_live_run_is_left_alone(session, parked):
    session.add(
        AgentRun(
            run_code="run-parked",
            ticket_id=parked.id,
            workspace_id=parked.workspace_id,
            agent_id="backend_implementer",
            stage_key="done",
            status=RunStatus.RUNNING,
        )
    )
    session.commit()

    assert finish_parked_terminal_tickets(session) == []
    session.refresh(parked)
    assert parked.state == TicketState.IN_PROGRESS


def test_a_ticket_with_a_live_orchestration_is_left_to_it(session, parked):
    session.add(
        OrchestrationRun(
            run_code="orch-parked",
            ticket_id=parked.id,
            workspace_id=parked.workspace_id,
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    session.commit()

    assert finish_parked_terminal_tickets(session) == []
    session.refresh(parked)
    assert parked.state == TicketState.IN_PROGRESS


def test_a_hand_set_state_is_not_overwritten(session, parked):
    parked.state_locked = True
    session.add(parked)
    session.commit()

    assert finish_parked_terminal_tickets(session) == []
    session.refresh(parked)
    assert parked.state == TicketState.IN_PROGRESS


def test_a_ticket_still_short_of_its_terminal_stage_is_not_finished(session, parked):
    """The cursor, not the row's status, decides — a mid-workflow stage reads
    PENDING too, and finishing one would skip every stage after it."""
    parked.workflow_stage_key = "work"
    parked.workflow_stage_status = StageStatus.PENDING
    session.add(parked)
    session.commit()

    assert finish_parked_terminal_tickets(session) == []
    session.refresh(parked)
    assert parked.state == TicketState.IN_PROGRESS
