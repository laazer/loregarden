"""A lane hold that ends without its repair leaves the ticket blocked.

The hold re-arms the blocked stage (PENDING) so the ticket reads in progress
while its lane waits. When the hold settled instead — a cap spent, the lane
reclaimed — the stage stayed re-armed with nothing left to run it, and the
ticket read in progress indefinitely (lg-milestone-that-1420).
"""

from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentSlot,
    BlockKind,
    OrchestrationRun,
    QueuedRun,
    QueuePosition,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services import queue_lanes
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.invariant_registry import SETTLED_HOLDS_READ_BLOCKED
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.queue_repair import REPAIR_ATTEMPT_CAP, REPAIR_WALL_CLOCK
from loregarden.services.queue_repair_settle import settled_holds_left_rearmed
from sqlmodel import Session, select
from tests.test_block_repair import (  # noqa: F401 - fixtures are used by name
    WORKER,
    _script,
    ticket_fixture,
)
from tests.test_lane_repair_hold import _ClaimingDispatcher


def _held(db_session: Session, ticket: Ticket, monkeypatch) -> tuple[QueueLaneService, QueuedRun]:
    """Run the ticket's failing stage in lane 1 so the lane is held for a retry."""
    _script(monkeypatch, {WORKER: (RunStatus.FAILED, "", "Error: something broke")})
    lanes = QueueLaneService(
        db_session, max_concurrent=3, dispatcher=_ClaimingDispatcher(db_session)
    )
    lanes.add_to_lane(ticket_id=ticket.id, slot_number=1, auto_repair=False)
    BuiltinOrchestrator(db_session).execute(
        ticket, OrchestrationProfile(slug="settle-test"), max_stages=10, auto_repair=False
    )
    entry = db_session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).one()
    db_session.refresh(ticket)
    assert entry.status == QueuePosition.REPAIRING
    assert ticket.state is TicketState.IN_PROGRESS, "the hold re-armed the stage"
    return lanes, entry


def _slot(db_session: Session, number: int) -> AgentSlot:
    return db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == number)).one()


def _spend_attempts(db_session: Session, entry: QueuedRun) -> None:
    entry.repair_attempts = REPAIR_ATTEMPT_CAP
    db_session.add(entry)
    db_session.commit()


def _spend_wall_clock(db_session: Session, entry: QueuedRun) -> None:
    entry.repairing_since = datetime.now(timezone.utc) - REPAIR_WALL_CLOCK - timedelta(seconds=1)
    db_session.add(entry)
    db_session.commit()


def _reclaim_lane(db_session: Session, entry: QueuedRun) -> None:
    slot = _slot(db_session, 1)
    slot.current_orchestration_run_id = None
    slot.is_available = True
    db_session.add(slot)
    db_session.commit()


@pytest.mark.parametrize(
    "end_hold",
    [_spend_attempts, _spend_wall_clock, _reclaim_lane],
    ids=["attempt-cap", "wall-clock-cap", "lane-reclaimed"],
)
def test_every_way_a_hold_settles_leaves_the_ticket_blocked(
    db_session, ticket, monkeypatch, end_hold
):
    lanes, entry = _held(db_session, ticket, monkeypatch)
    held_kind = entry.held_block_kind
    assert held_kind is not None, "the hold records the kind the re-arm clears"

    end_hold(db_session, entry)
    lanes.reconcile_lanes()

    db_session.refresh(entry)
    db_session.refresh(ticket)
    assert entry.status == QueuePosition.STARTED
    assert ticket.state is TicketState.BLOCKED
    assert ticket.workflow_stage_status is StageStatus.BLOCKED
    assert ticket.block_kind is held_kind
    assert settled_holds_left_rearmed(db_session) == []


def test_a_hold_ended_by_a_restart_elsewhere_leaves_the_restart_running(
    db_session, ticket, monkeypatch
):
    """The run that replaced the hold owns the ticket; blocking it would stop it."""
    lanes, entry = _held(db_session, ticket, monkeypatch)
    other = OrchestrationRun(
        run_code="resumed", ticket_id=ticket.id, workspace_id=ticket.workspace_id
    )
    db_session.add(other)
    db_session.commit()
    slot3 = _slot(db_session, 3)
    slot3.is_available = False
    slot3.current_orchestration_run_id = other.id
    db_session.add(slot3)
    db_session.commit()

    lanes.reconcile_lanes()

    db_session.refresh(entry)
    db_session.refresh(ticket)
    assert entry.status == QueuePosition.STARTED
    assert ticket.state is TicketState.IN_PROGRESS


def test_a_hold_whose_ticket_was_closed_does_not_reopen_it(db_session, ticket, monkeypatch):
    lanes, entry = _held(db_session, ticket, monkeypatch)
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()

    lanes.reconcile_lanes()

    db_session.refresh(entry)
    db_session.refresh(ticket)
    assert entry.status == QueuePosition.STARTED
    assert ticket.state is TicketState.DONE


def test_a_settled_hold_left_rearmed_is_found_and_blocked(db_session, ticket, monkeypatch):
    """The invariant's half, for rows the old settle wrote: no kind recorded,
    stage re-armed, nothing running. Detected, then blocked with the kind the
    block message supports."""
    lanes, entry = _held(db_session, ticket, monkeypatch)
    entry.held_block_kind = None
    _spend_attempts(db_session, entry)
    with mock.patch.object(queue_lanes, "reblock_settled", return_value=False):
        lanes.reconcile_lanes()
    db_session.refresh(ticket)
    assert ticket.state is TicketState.IN_PROGRESS, "the pre-fix settle, reproduced"

    violations = SETTLED_HOLDS_READ_BLOCKED.detect(db_session)
    assert [v.ticket_id for v in violations] == [ticket.id]
    assert SETTLED_HOLDS_READ_BLOCKED.repair(db_session, violations) == 1

    db_session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is not None
    assert ticket.block_kind in BlockKind
    assert SETTLED_HOLDS_READ_BLOCKED.detect(db_session) == []


def test_a_ticket_requeued_after_its_hold_settled_is_not_reported(db_session, ticket, monkeypatch):
    """A newer queue entry means someone asked for another run: not stranded."""
    lanes, entry = _held(db_session, ticket, monkeypatch)
    _spend_attempts(db_session, entry)
    with mock.patch.object(queue_lanes, "reblock_settled", return_value=False):
        lanes.reconcile_lanes()
    db_session.add(
        QueuedRun(
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            slot_number=2,
            status=QueuePosition.QUEUED,
        )
    )
    db_session.commit()

    assert SETTLED_HOLDS_READ_BLOCKED.detect(db_session) == []


def test_a_hold_that_settles_while_the_ticket_waits_in_another_lane_stays_armed(
    db_session, ticket, monkeypatch
):
    """The queued entry will run the re-armed stage; blocking it would stop that."""
    lanes, entry = _held(db_session, ticket, monkeypatch)
    db_session.add(
        QueuedRun(
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            slot_number=2,
            status=QueuePosition.QUEUED,
        )
    )
    _spend_attempts(db_session, entry)

    lanes._settle_repair(entry, "the attempt cap is spent")

    db_session.refresh(ticket)
    assert ticket.state is TicketState.IN_PROGRESS
