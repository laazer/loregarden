"""A leftover RUNNING child is not live work once its orchestration has ended.

External-harness reviewers do not renew a lease, so the lease reaper never
touches them. The parent going terminal is the signal they are residue — the
row that made ticket 546 read Running on the home board after the queue was
already idle.
"""

from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    Ticket,
    WorkItemType,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.reconciliation import reconcile_once
from loregarden.services.run_interruption import ORPHAN_OF_TERMINAL_ORCH_MESSAGE
from loregarden.services.run_service import settle_orphaned_agent_runs
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session


def _ticket(session: Session) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug="loregarden",
        title="orphan child run",
        work_item_type=WorkItemType.MILESTONE,
    )


def _orch(session: Session, ticket: Ticket, status: OrchestrationRunStatus) -> OrchestrationRun:
    run = OrchestrationRun(
        run_code="orch-orphan",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=status,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _child(session: Session, ticket: Ticket, orch: OrchestrationRun) -> AgentRun:
    run = AgentRun(
        run_code="run-orphan-child",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        status=RunStatus.RUNNING,
        orchestration_run_id=orch.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_running_child_of_a_failed_orchestration_is_failed(db_session):
    ticket = _ticket(db_session)
    orch = _orch(db_session, ticket, OrchestrationRunStatus.FAILED)
    child = _child(db_session, ticket, orch)

    settled = settle_orphaned_agent_runs(db_session)

    assert [run.id for run in settled] == [child.id]
    db_session.refresh(child)
    assert child.status == RunStatus.FAILED
    assert child.stderr == ORPHAN_OF_TERMINAL_ORCH_MESSAGE


def test_a_running_child_of_a_live_orchestration_is_spared(db_session):
    ticket = _ticket(db_session)
    orch = _orch(db_session, ticket, OrchestrationRunStatus.RUNNING)
    child = _child(db_session, ticket, orch)

    assert settle_orphaned_agent_runs(db_session) == []
    db_session.refresh(child)
    assert child.status == RunStatus.RUNNING


def test_finishing_an_orchestration_fails_its_in_flight_children(db_session):
    ticket = _ticket(db_session)
    orch = _orch(db_session, ticket, OrchestrationRunStatus.RUNNING)
    child = _child(db_session, ticket, orch)

    OrchestrationCallbackService(db_session).complete_orchestration(
        orch, ticket, status=OrchestrationRunStatus.FAILED, message="stopped"
    )

    db_session.refresh(child)
    assert child.status == RunStatus.FAILED
    assert child.stderr == ORPHAN_OF_TERMINAL_ORCH_MESSAGE


def test_the_periodic_sweep_settles_orphans_and_spares_live_work(db_session):
    ticket = _ticket(db_session)
    dead = _orch(db_session, ticket, OrchestrationRunStatus.FAILED)
    orphan = _child(db_session, ticket, dead)
    live_ticket = TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="live sibling",
        work_item_type=WorkItemType.MILESTONE,
    )
    live_orch = OrchestrationRun(
        run_code="orch-live",
        ticket_id=live_ticket.id,
        workspace_id=live_ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(live_orch)
    db_session.commit()
    db_session.refresh(live_orch)
    live = AgentRun(
        run_code="run-live-child",
        ticket_id=live_ticket.id,
        workspace_id=live_ticket.workspace_id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
        orchestration_run_id=live_orch.id,
    )
    db_session.add(live)
    db_session.commit()

    assert reconcile_once(db_session) == []

    db_session.refresh(orphan)
    db_session.refresh(live)
    assert orphan.status == RunStatus.FAILED
    assert live.status == RunStatus.RUNNING
