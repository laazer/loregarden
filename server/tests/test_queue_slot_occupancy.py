"""What a slot reports, and when it lets go.

Two ways the board lied about a slot. A run that finished without its slot
being released left the lane pinned to a terminal run forever, so slot 1 read
"succeeded" and nothing could ever start there again. And a lane held by an
orchestration was keyed on `current_run_id`, which a lane never sets, so an
occupied slot drew as "Available" and offered to start a second ticket in it.
"""

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.queue_status import build_queue_status
from sqlmodel import Session, select


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session: Session, workspace_id: str, code: str) -> Ticket:
    ticket = Ticket(external_id=code, workspace_id=workspace_id, title=f"Ticket {code}")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _run(session: Session, ticket: Ticket, code: str, **kwargs) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
        **kwargs,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _orch(session: Session, ticket: Ticket, code: str, **kwargs) -> OrchestrationRun:
    orch_run = OrchestrationRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        **kwargs,
    )
    session.add(orch_run)
    session.commit()
    session.refresh(orch_run)
    return orch_run


def _occupy(session: Session, slot_number: int, **held) -> AgentSlot:
    """Pin a slot to whatever `held` names, as a leaked release would leave it."""
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == slot_number)).one()
    slot.is_available = False
    for field, value in held.items():
        setattr(slot, field, value)
    session.add(slot)
    session.commit()
    session.refresh(slot)
    return slot


def _lanes(session: Session) -> QueueLaneService:
    service = QueueLaneService(session, max_concurrent=3)
    service.slots.initialize_slots()
    return service


def test_a_slot_pinned_to_a_finished_run_is_reclaimed(session, workspace):
    lanes = _lanes(session)
    ticket = _ticket(session, workspace.id, "T-1")
    run = _run(session, ticket, "run_done", status=RunStatus.SUCCEEDED)
    _occupy(session, 1, current_run_id=run.id)

    assert lanes.reconcile_lanes() == [1]

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available
    assert slot.current_run_id is None


def test_a_slot_running_a_live_run_is_left_alone(session, workspace):
    lanes = _lanes(session)
    ticket = _ticket(session, workspace.id, "T-1")
    run = _run(session, ticket, "run_live", status=RunStatus.RUNNING)
    _occupy(session, 1, current_run_id=run.id)

    assert lanes.reconcile_lanes() == []
    assert not session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one().is_available


def test_a_lane_whose_orchestration_finished_is_reclaimed(session, workspace):
    lanes = _lanes(session)
    ticket = _ticket(session, workspace.id, "T-1")
    orch_run = _orch(session, ticket, "orch_done", status=OrchestrationRunStatus.SUCCEEDED)
    _occupy(session, 2, current_orchestration_run_id=orch_run.id)

    assert lanes.reconcile_lanes() == [2]

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 2)).one()
    assert slot.is_available
    assert slot.current_orchestration_run_id is None


def test_reclaiming_a_lane_starts_what_was_queued_behind_it(session, workspace):
    lanes = _lanes(session)
    finished = _ticket(session, workspace.id, "T-1")
    orch_run = _orch(session, finished, "orch_done", status=OrchestrationRunStatus.FAILED)
    _occupy(session, 1, current_orchestration_run_id=orch_run.id)

    waiting_ticket = _ticket(session, workspace.id, "T-2")
    waiting = QueuedRun(
        workspace_id=workspace.id,
        ticket_id=waiting_ticket.id,
        slot_number=1,
        position=1,
        status=QueuePosition.QUEUED,
    )
    session.add(waiting)
    session.commit()

    started = _orch(session, waiting_ticket, "orch_next")
    lanes._dispatch_orchestration = lambda ticket, **kwargs: started

    assert lanes.reconcile_lanes() == [1]
    session.refresh(waiting)
    assert waiting.status == QueuePosition.ACTIVE


@pytest.mark.asyncio
async def test_the_board_never_reports_a_finished_run_as_running(session, workspace):
    lanes = _lanes(session)
    ticket = _ticket(session, workspace.id, "T-1")
    run = _run(session, ticket, "run_done", status=RunStatus.SUCCEEDED)
    _occupy(session, 1, current_run_id=run.id)

    status = await build_queue_status(session)

    assert status["active_runs"] == []
    assert [lane["running"] for lane in status["lanes"]] == [None, None, None]
    assert lanes.waiting_in_lane(1) == []


@pytest.mark.asyncio
async def test_a_lane_running_a_ticket_is_reported_as_busy(session, workspace):
    _lanes(session)
    ticket = _ticket(session, workspace.id, "T-1")
    orch_run = _orch(session, ticket, "orch_live", status=OrchestrationRunStatus.RUNNING)
    _run(session, ticket, "run_stage", status=RunStatus.RUNNING, orchestration_run_id=orch_run.id)
    _occupy(session, 1, current_orchestration_run_id=orch_run.id)

    status = await build_queue_status(session)

    lane = next(lane for lane in status["lanes"] if lane["slot_number"] == 1)
    assert lane["running"] is not None
    assert lane["running"]["ticket_id"] == ticket.id
    assert lane["running"]["ticket_title"] == "Ticket T-1"
    assert lane["running"]["stage_key"] == "implement"
    assert lane["running"]["status"] == "running"


@pytest.mark.asyncio
async def test_a_lane_between_stages_still_reads_as_running(session, workspace):
    """The slot is held for the whole ticket, so the last stage's status is not the lane's."""
    _lanes(session)
    ticket = _ticket(session, workspace.id, "T-1")
    orch_run = _orch(session, ticket, "orch_live", status=OrchestrationRunStatus.RUNNING)
    _run(
        session,
        ticket,
        "run_stage",
        status=RunStatus.SUCCEEDED,
        orchestration_run_id=orch_run.id,
    )
    _occupy(session, 1, current_orchestration_run_id=orch_run.id)

    status = await build_queue_status(session)

    lane = next(lane for lane in status["lanes"] if lane["slot_number"] == 1)
    assert lane["running"]["status"] == "running"
    # No agent median can measure a whole pipeline, so the card says unknown
    # rather than drawing a bar against one stage's history.
    assert lane["running"]["estimated_duration_seconds"] is None


def test_a_slot_marked_busy_but_holding_nothing_is_reclaimed(session, workspace):
    lanes = _lanes(session)
    _occupy(session, 3)

    assert lanes.reconcile_lanes() == [3]
    assert session.exec(select(AgentSlot).where(AgentSlot.slot_number == 3)).one().is_available


def test_reconcile_leaves_untouched_pools_alone(session, workspace):
    lanes = _lanes(session)
    assert lanes.reconcile_lanes() == []
    assert all(slot.is_available for slot in session.exec(select(AgentSlot)).all())


def _lane_entry(session: Session, ticket: Ticket, slot_number: int, **kwargs) -> QueuedRun:
    """A ticket waiting in a lane: no run of its own until the lane starts one."""
    entry = QueuedRun(
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        slot_number=slot_number,
        position=1,
        **{"status": QueuePosition.QUEUED, **kwargs},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def test_the_shared_queue_does_not_promote_lane_entries(session, workspace):
    """A lane entry names a ticket, not a run — promoting it starts nothing."""
    queue = ParallelQueueService(session, max_concurrent=3)
    queue.initialize_slots()
    ticket = _ticket(session, workspace.id, "T-1")
    entry = _lane_entry(session, ticket, 2)

    assert queue.promote_from_queue_sync() is None

    session.refresh(entry)
    assert entry.status == QueuePosition.QUEUED
    assert all(slot.is_available for slot in session.exec(select(AgentSlot)).all())


def test_promotion_leaves_lane_positions_alone(session, workspace):
    """Lane positions are per lane; the shared line must not renumber them."""
    queue = ParallelQueueService(session, max_concurrent=3)
    queue.initialize_slots()
    ticket = _ticket(session, workspace.id, "T-1")
    entry = _lane_entry(session, ticket, 2)
    entry.position = 4
    session.add(entry)
    session.commit()

    waiting_run = _run(session, ticket, "run_waiting", status=RunStatus.QUEUED)
    _lane_entry(session, ticket, 1, run_id=waiting_run.id)

    # Promotion dispatches for real, which would branch and launch an agent.
    with patch("loregarden.services.run_service.schedule_agent_run"):
        queue.promote_from_queue_sync()

    session.refresh(entry)
    assert entry.position == 4


def test_a_lane_entry_started_by_nothing_goes_back_in_line(session, workspace):
    """The residue of a stolen entry: slot claimed, ticket gone from its lane."""
    lanes = _lanes(session)
    ticket = _ticket(session, workspace.id, "T-1")
    entry = _lane_entry(session, ticket, 2, status=QueuePosition.PROMOTED)
    _occupy(session, 2)

    started = _orch(session, ticket, "orch_next")
    lanes._dispatch_orchestration = lambda ticket, **kwargs: started

    assert lanes.reconcile_lanes() == [2]

    session.refresh(entry)
    assert entry.status == QueuePosition.ACTIVE
    assert entry.orchestration_run_id == started.id


def test_a_lane_entry_that_is_genuinely_running_is_left_alone(session, workspace):
    lanes = _lanes(session)
    ticket = _ticket(session, workspace.id, "T-1")
    orch_run = _orch(session, ticket, "orch_live", status=OrchestrationRunStatus.RUNNING)
    entry = _lane_entry(
        session,
        ticket,
        1,
        status=QueuePosition.ACTIVE,
        orchestration_run_id=orch_run.id,
    )
    _occupy(session, 1, current_orchestration_run_id=orch_run.id)

    assert lanes.reconcile_lanes() == []

    session.refresh(entry)
    assert entry.status == QueuePosition.ACTIVE


def test_a_slot_release_survives_a_failing_completion_tail(session, workspace):
    """The tail is best-effort; the slot it holds is not the tail's to keep."""
    from loregarden.services.orchestration import OrchestrationService

    ticket = _ticket(session, workspace.id, "T-1")
    run = _run(session, ticket, "run_live", status=RunStatus.RUNNING)
    ParallelQueueService(session, max_concurrent=3).initialize_slots()
    _occupy(session, 1, current_run_id=run.id)

    with patch(
        "loregarden.services.orchestration.complete_run_tail",
        side_effect=RuntimeError("artifact refresh blew up"),
    ):
        OrchestrationService(session).complete_run(run, status=RunStatus.SUCCEEDED)

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available
    assert slot.current_run_id is None
