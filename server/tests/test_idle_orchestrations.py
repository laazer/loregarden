"""A ticket whose runs keep opening and starting no agent is held, not opened again."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    BlockKind,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.autopilot_retry import retry_reason
from loregarden.services.idle_run_invariant import unheld_idle_tickets
from loregarden.services.interruption_messages import expired_lease_message
from loregarden.services.queue_repair import resolve_repair_route
from loregarden.services.run_concurrency import ORCHESTRATION_LEASE
from loregarden.services.run_service import settle_expired_orchestration_leases
from sqlmodel import Session, select

NOW = datetime.now(timezone.utc)
EXPIRED = ORCHESTRATION_LEASE + timedelta(minutes=5)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="ticket")
def ticket_fixture(session) -> Ticket:
    workspace = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(workspace)
    session.commit()
    ticket = Ticket(
        external_id="proj-1", workspace_id=workspace.id, title="t", state=TicketState.BACKLOG
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _run(
    session: Session,
    ticket: Ticket,
    code: str,
    *,
    age: timedelta = EXPIRED,
    status: OrchestrationRunStatus = OrchestrationRunStatus.RUNNING,
    error: str = "",
    last_seen: timedelta | None = None,
) -> OrchestrationRun:
    run = OrchestrationRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        driver=OrchestrationDriver.EXTERNAL_MCP,
        status=status,
        error_message=error,
        started_at=NOW - age,
        created_at=NOW - age,
        last_seen_at=None if last_seen is None else NOW - last_seen,
        finished_at=None if status is OrchestrationRunStatus.RUNNING else NOW - age / 2,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _expired(
    session: Session, ticket: Ticket, code: str = "orch_old", **fields
) -> OrchestrationRun:
    return _run(
        session,
        ticket,
        code,
        age=EXPIRED * 2,
        status=OrchestrationRunStatus.FAILED,
        error=expired_lease_message(code, 0),
        **fields,
    )


def _agent(session: Session, ticket: Ticket, run: OrchestrationRun) -> None:
    session.add(
        AgentRun(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            run_code=f"run_{run.run_code}",
            agent_id="planner",
            stage_key="plan",
            status=RunStatus.FAILED,
            orchestration_run_id=run.id,
            created_at=run.created_at,
        )
    )
    session.commit()


def _sweep(session: Session, ticket: Ticket, run: OrchestrationRun) -> None:
    assert [r.id for r in settle_expired_orchestration_leases(session)] == [run.id]
    session.refresh(ticket)
    session.refresh(run)


def test_one_idle_run_is_not_a_pattern(session, ticket):
    run = _run(session, ticket, "orch_1")
    _sweep(session, ticket, run)
    assert run.status is OrchestrationRunStatus.FAILED
    assert ticket.state is TicketState.BACKLOG


def test_the_second_idle_run_in_a_row_holds_the_ticket(session, ticket):
    _expired(session, ticket)
    run = _run(session, ticket, "orch_2")

    _sweep(session, ticket, run)

    assert run.status is OrchestrationRunStatus.BLOCKED
    assert run.finished_at is not None
    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.HUMAN_ACTION
    assert "orch_2" in ticket.blocking_issues and "external_mcp" in ticket.blocking_issues
    assert unheld_idle_tickets(session) == []


def test_the_hold_reaches_the_inbox_and_nothing_retries_it(session, ticket):
    _expired(session, ticket)
    _sweep(session, ticket, _run(session, ticket, "orch_2"))

    card = session.exec(select(Approval).where(Approval.ticket_id == ticket.id)).one()
    assert (card.kind, card.status) == (ApprovalKind.HUMAN_ACTION, ApprovalStatus.PENDING)
    assert card.checklist_json == "[]"
    assert resolve_repair_route(session, ticket) is None
    assert retry_reason(session, ticket) is None


def test_a_requeued_ticket_gets_its_two_tries_back(session, ticket):
    _expired(session, ticket)
    _sweep(session, ticket, _run(session, ticket, "orch_2", age=EXPIRED * 1.5))
    ticket.state = TicketState.BACKLOG
    session.add(ticket)
    session.commit()

    _sweep(session, ticket, _run(session, ticket, "orch_3"))

    assert ticket.state is TicketState.BACKLOG


@pytest.mark.parametrize("which", ["earlier", "latest"])
def test_a_run_that_started_an_agent_breaks_the_streak(session, ticket, which):
    earlier = _expired(session, ticket)
    run = _run(session, ticket, "orch_2")
    _agent(session, ticket, earlier if which == "earlier" else run)

    _sweep(session, ticket, run)

    assert run.status is OrchestrationRunStatus.FAILED
    assert ticket.state is TicketState.BACKLOG


@pytest.mark.parametrize("which", ["earlier", "latest"])
def test_a_run_something_renewed_breaks_the_streak(session, ticket, which):
    """A terminal agent moves stages without starting an agent run; each move renews."""
    _expired(session, ticket, last_seen=EXPIRED * 1.9 if which == "earlier" else None)
    run = _run(session, ticket, "orch_2", last_seen=EXPIRED if which == "latest" else None)

    _sweep(session, ticket, run)

    assert ticket.state is TicketState.BACKLOG


def test_a_parent_whose_childs_agents_worked_is_not_idle(session, ticket):
    child = Ticket(
        external_id="proj-2",
        workspace_id=ticket.workspace_id,
        title="c",
        parent_ticket_id=ticket.id,
    )
    session.add(child)
    session.commit()
    _expired(session, ticket)
    run = _run(session, ticket, "orch_2")
    nested = _run(
        session,
        child,
        "orch_child",
        age=EXPIRED - timedelta(minutes=1),
        status=OrchestrationRunStatus.SUCCEEDED,
    )
    _agent(session, child, nested)

    _sweep(session, ticket, run)

    assert ticket.state is TicketState.BACKLOG


def test_a_childs_old_agents_do_not_vouch_for_a_later_run(session, ticket):
    child = Ticket(
        external_id="proj-2",
        workspace_id=ticket.workspace_id,
        title="c",
        parent_ticket_id=ticket.id,
    )
    session.add(child)
    session.commit()
    nested = _run(
        session, child, "orch_child", age=EXPIRED * 3, status=OrchestrationRunStatus.SUCCEEDED
    )
    _agent(session, child, nested)
    _expired(session, ticket)

    _sweep(session, ticket, _run(session, ticket, "orch_2"))

    assert ticket.state is TicketState.BLOCKED


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (OrchestrationRunStatus.FAILED, "Interrupted by a server restart"),
        (OrchestrationRunStatus.CANCELLED, "Orchestration lease expired: by hand"),
        (OrchestrationRunStatus.SUCCEEDED, ""),
    ],
)
def test_an_earlier_run_that_ended_another_way_is_not_counted(session, ticket, status, error):
    _run(session, ticket, "orch_old", age=EXPIRED * 2, status=status, error=error)
    _sweep(session, ticket, _run(session, ticket, "orch_2"))
    assert ticket.state is TicketState.BACKLOG


def test_a_run_between_two_idle_ones_breaks_the_streak(session, ticket):
    _expired(session, ticket)
    _run(
        session,
        ticket,
        "orch_worked",
        age=EXPIRED * 1.5,
        status=OrchestrationRunStatus.SUCCEEDED,
    )
    _sweep(session, ticket, _run(session, ticket, "orch_3"))
    assert ticket.state is TicketState.BACKLOG


def test_another_tickets_idle_run_is_not_counted(session, ticket):
    other = Ticket(external_id="proj-9", workspace_id=ticket.workspace_id, title="o")
    session.add(other)
    session.commit()
    _expired(session, other)
    _sweep(session, ticket, _run(session, ticket, "orch_2"))
    assert ticket.state is TicketState.BACKLOG


@pytest.mark.parametrize("state", [TicketState.PARKED, TicketState.DONE, TicketState.BLOCKED])
def test_a_ticket_already_stopped_keeps_its_own_reason(session, ticket, state):
    ticket.state = state
    ticket.blocking_issues = "its own reason"
    session.add(ticket)
    session.commit()
    _expired(session, ticket)

    run = _run(session, ticket, "orch_2")
    _sweep(session, ticket, run)

    assert run.status is OrchestrationRunStatus.FAILED
    assert (ticket.state, ticket.blocking_issues) == (state, "its own reason")


def test_the_invariant_finds_a_ticket_no_sweep_held(session, ticket):
    _expired(session, ticket, "orch_1")
    latest = _run(
        session,
        ticket,
        "orch_2",
        status=OrchestrationRunStatus.FAILED,
        error=expired_lease_message("orch_2", 0),
    )

    (violation,) = unheld_idle_tickets(session)
    assert violation.ticket_id == ticket.id
    assert violation.entered_at == latest.finished_at


def test_the_invariant_clears_when_a_new_run_opens(session, ticket):
    _expired(session, ticket, "orch_1")
    _run(
        session,
        ticket,
        "orch_2",
        status=OrchestrationRunStatus.FAILED,
        error=expired_lease_message("orch_2", 0),
    )
    _run(session, ticket, "orch_3", age=timedelta(minutes=1))
    assert unheld_idle_tickets(session) == []


@pytest.mark.parametrize(
    "state", [TicketState.BLOCKED, TicketState.PARKED, TicketState.DONE, TicketState.WONT_DO]
)
def test_the_invariant_leaves_a_ticket_already_stopped(session, ticket, state):
    _expired(session, ticket, "orch_1")
    _run(
        session,
        ticket,
        "orch_2",
        status=OrchestrationRunStatus.FAILED,
        error=expired_lease_message("orch_2", 0),
    )
    ticket.state = state
    session.add(ticket)
    session.commit()
    assert unheld_idle_tickets(session) == []


def test_the_invariant_leaves_one_idle_run_alone(session, ticket):
    _expired(session, ticket)
    assert unheld_idle_tickets(session) == []
