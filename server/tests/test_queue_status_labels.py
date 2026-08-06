"""What a lane card is allowed to claim.

The queue's own tables only know who holds a slot. Whether the work behind that
slot is still moving comes from the run tables, so the snapshot has to carry it
— otherwise a card cannot tell a working lane from a stuck one.
"""

from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.queue_status import build_queue_status
from sqlmodel import Session, select


def _workspace(session: Session) -> Workspace:
    return session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


def _ticket(session: Session, ws: Workspace, code: str, state: TicketState) -> Ticket:
    ticket = Ticket(external_id=code, workspace_id=ws.id, title=f"Ticket {code}", state=state)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _occupy_slot(session: Session, ticket: Ticket, status: RunStatus, slot_number: int = 1) -> None:
    """Put a run in a slot, exactly as the dispatcher does."""
    run = AgentRun(
        run_code=f"run-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=status,
    )
    session.add(run)
    session.commit()

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == slot_number)).first()
    if slot is None:
        slot = AgentSlot(slot_number=slot_number)
    slot.is_available = False
    slot.current_run_id = run.id
    session.add(slot)
    session.commit()


async def test_a_lane_card_carries_the_ticket_state_and_activity(db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "LANE-1", TicketState.IN_PROGRESS)
    _occupy_slot(db_session, ticket, RunStatus.RUNNING)

    snapshot = await build_queue_status(db_session)
    card = next(run for run in snapshot["active_runs"] if run["ticket_id"] == ticket.id)

    assert card["ticket_state"] == "in_progress"
    assert card["ticket_activity"] == "running"


async def test_a_lane_card_carries_ticket_ancestry_and_running_descendant(db_session):
    """Parent holds the lane; nested child execute is what the card must name."""
    from loregarden.models.domain import OrchestrationRun, OrchestrationRunStatus

    ws = _workspace(db_session)
    parent = _ticket(db_session, ws, "PARENT-1", TicketState.IN_PROGRESS)
    child = _ticket(db_session, ws, "CHILD-1", TicketState.IN_PROGRESS)
    child.parent_ticket_id = parent.id
    db_session.add(child)
    db_session.commit()

    parent_orch = OrchestrationRun(
        run_code="orch-parent",
        ticket_id=parent.id,
        workspace_id=ws.id,
        status=OrchestrationRunStatus.RUNNING,
    )
    child_orch = OrchestrationRun(
        run_code="orch-child",
        ticket_id=child.id,
        workspace_id=ws.id,
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(parent_orch)
    db_session.add(child_orch)
    db_session.commit()

    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).first()
    if slot is None:
        slot = AgentSlot(slot_number=1)
    slot.is_available = False
    slot.current_orchestration_run_id = parent_orch.id
    db_session.add(slot)
    db_session.commit()

    snapshot = await build_queue_status(db_session)
    card = next(run for run in snapshot["active_runs"] if run["ticket_id"] == parent.id)

    assert [node["code"] for node in card["ticket_ancestry"]] == ["PARENT-1"]
    assert card["running_descendant"]["code"] == "CHILD-1"
    assert card["running_descendant"]["title"] == "Ticket CHILD-1"


async def test_a_slot_held_by_a_finished_run_is_reclaimed(db_session):
    """The slot leak, closed rather than described.

    This used to assert the card an operator saw while a slot sat pinned to a
    run that had already finished: occupied, status "succeeded", nothing
    working. That state is now unreachable — building the snapshot reconciles
    the pool first, and a slot whose only occupant is a terminal run is free by
    definition — so the honest assertion is that there is no card at all and the
    lane is back.

    A lane between stages is not this: it holds an orchestration, which stays
    live across the agent runs it spans.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "LANE-2", TicketState.IN_PROGRESS)
    _occupy_slot(db_session, ticket, RunStatus.SUCCEEDED)

    snapshot = await build_queue_status(db_session)

    assert not [run for run in snapshot["active_runs"] if run["ticket_id"] == ticket.id]
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available
    assert slot.current_run_id is None


async def test_a_waiting_entry_carries_its_own_state(db_session):
    ws = _workspace(db_session)
    waiting = _ticket(db_session, ws, "LANE-3", TicketState.BLOCKED)
    db_session.add(
        QueuedRun(
            workspace_id=ws.id,
            ticket_id=waiting.id,
            slot_number=1,
            position=1,
            status=QueuePosition.QUEUED,
        )
    )
    db_session.commit()

    snapshot = await build_queue_status(db_session)
    entries = [entry for lane in snapshot["lanes"] for entry in lane["waiting"]]
    entry = next(e for e in entries if e["ticket_id"] == waiting.id)

    # Blocked, and queued behind whatever holds the lane — both worth seeing
    # before you wonder why the lane is not moving.
    assert entry["ticket_state"] == "blocked"
    assert entry["ticket_activity"] == "queued"
