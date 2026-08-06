"""Max agent runtime rides with the orchestration ask."""

from unittest.mock import patch

from loregarden.models.domain import (
    AgentSlot,
    OrchestrationRun,
    QueuedRun,
    QueuePosition,
    Ticket,
    Workspace,
)
from loregarden.services.queue_lanes import QueueLaneService
from sqlmodel import Session, select


def _workspace(session: Session) -> Workspace:
    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    return ws


def _ticket(session: Session, ws: Workspace, code: str) -> Ticket:
    ticket = Ticket(
        external_id=code,
        workspace_id=ws.id,
        title=f"Ticket {code}",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def test_add_to_lane_stores_timeout_on_the_entry(db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "TO-1")
    lanes = QueueLaneService(db_session, max_concurrent=3)
    lanes.slots.initialize_slots()
    # Fill the lane so the entry parks instead of dispatching.
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    slot.is_available = False
    db_session.add(slot)
    db_session.commit()

    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1, timeout_seconds=900)

    entry = db_session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).one()
    assert entry.timeout_seconds == 900
    assert entry.status == QueuePosition.QUEUED


def test_lane_dispatch_passes_timeout_into_orchestration(db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "TO-2")
    lanes = QueueLaneService(db_session, max_concurrent=3)
    lanes.slots.initialize_slots()
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    slot.is_available = False
    db_session.add(slot)
    db_session.commit()

    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1, timeout_seconds=600)
    slot.is_available = True
    slot.current_orchestration_run_id = None
    db_session.add(slot)
    db_session.commit()

    with patch("loregarden.services.run_service.schedule_orchestration") as schedule:
        lanes.start_lane_head(1)

    assert schedule.called
    assert schedule.call_args.kwargs["timeout_seconds"] == 600
    claim = db_session.exec(
        select(OrchestrationRun).where(OrchestrationRun.ticket_id == ticket.id)
    ).one()
    assert claim.timeout_override_seconds == 600
