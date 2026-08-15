"""What a lane card is allowed to claim.

The queue's own tables only know who holds a slot. Whether the work behind that
slot is still moving comes from the run tables, so the snapshot has to carry it
— otherwise a card cannot tell a working lane from a stuck one.
"""

from datetime import datetime, timezone

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
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.queue_status import build_queue_status
from loregarden.services.reconciliation import reconcile_once
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
    """The slot leak, closed rather than described — now in two places.

    This used to assert the card an operator saw while a slot sat pinned to a
    run that had already finished: occupied, status "succeeded", nothing
    working. Both halves of the fix are pinned separately because they are now
    owned by different things.

    The *read* must never draw that lane as running. It answers from
    `_occupant_is_live` rather than from the row, so it is right even between a
    run finishing and the next sweep.

    The *row* is reclaimed by the reconciliation pass. It used to be reclaimed
    by this very read, which tied repair to whether anyone had the dashboard
    open — see ticket 437.

    A lane between stages is not this: it holds an orchestration, which stays
    live across the agent runs it spans.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "LANE-2", TicketState.IN_PROGRESS)
    _occupy_slot(db_session, ticket, RunStatus.SUCCEEDED)

    snapshot = await build_queue_status(db_session)

    assert not [run for run in snapshot["active_runs"] if run["ticket_id"] == ticket.id]

    reconcile_once(db_session)

    db_session.expire_all()
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available
    assert slot.current_run_id is None


async def test_a_waiting_entry_carries_its_own_state(db_session):
    ws = _workspace(db_session)
    waiting = _ticket(db_session, ws, "LANE-3", TicketState.BLOCKED)
    # An entry only waits in a lane that is busy. `reconcile_lanes` starts the
    # head of any idle lane with work queued, because a refused dispatch used to
    # leave exactly that state with nothing to retry it.
    ParallelQueueService(db_session).initialize_slots()
    lane = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    lane.is_available = False
    lane.assigned_at = datetime.now(timezone.utc)
    db_session.add(lane)
    db_session.commit()
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


async def test_a_lane_carries_what_blocked_or_failed_in_it(db_session):
    """A lane that just ate a ticket must not look like one that never ran."""
    import json

    from loregarden.models.domain import OrchestrationRun, OrchestrationRunStatus

    ws = _workspace(db_session)
    stopped = _ticket(db_session, ws, "LANE-4", TicketState.BLOCKED)
    orch = OrchestrationRun(
        run_code="orch-stopped",
        ticket_id=stopped.id,
        workspace_id=ws.id,
        status=OrchestrationRunStatus.BLOCKED,
        current_stage_key="test_design",
        error_message="No workflow template",
    )
    db_session.add(orch)
    db_session.commit()
    db_session.add(
        QueuedRun(
            workspace_id=ws.id,
            ticket_id=stopped.id,
            orchestration_run_id=orch.id,
            slot_number=2,
            position=1,
            status=QueuePosition.STARTED,
        )
    )
    db_session.commit()

    snapshot = await build_queue_status(db_session)
    lane = next(lane for lane in snapshot["lanes"] if lane["slot_number"] == 2)

    assert lane["attention_total"] == 1
    card = lane["attention"][0]
    assert card["ticket_id"] == stopped.id
    assert card["outcome"] == "blocked"
    assert card["last_stage_key"] == "test_design"
    assert card["failure_reason"] == "No workflow template"
    # The websocket sends this snapshot with json.dumps, which has no opinion
    # about datetime other than raising.
    json.dumps(snapshot)

    other = next(lane for lane in snapshot["lanes"] if lane["slot_number"] == 1)
    assert other["attention"] == []
