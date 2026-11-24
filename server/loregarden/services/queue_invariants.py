"""Invariants over the lane queue: what an entry says against the work it names.

Split out of `invariant_registry`, which sits at its line cap and re-exports them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import (
    AgentRun,
    InvariantReentry,
    OrchestrationRun,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
)
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.parallel_queue import LIVE_ORCHESTRATION_STATUSES, WAITING_STATUSES
from loregarden.services.queue_repair import held_but_blocked, rearm_held
from loregarden.services.settled_hold_checks import (
    reblock_settled_violations,
    settled_hold_violations,
)
from sqlmodel import Session, col, or_, select

#: A shared-queue run is QUEUED while its entry waits, so only these are running.
_RUNNING = (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)


def _waiting_with_live_work(session: Session) -> list[QueuedRun]:
    return list(
        session.exec(
            select(QueuedRun)
            .outerjoin(AgentRun, col(AgentRun.id) == col(QueuedRun.run_id))
            .outerjoin(
                OrchestrationRun,
                col(OrchestrationRun.id) == col(QueuedRun.orchestration_run_id),
            )
            .where(col(QueuedRun.status).in_(WAITING_STATUSES))
            .where(
                or_(
                    col(AgentRun.status).in_(_RUNNING),
                    col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES),
                )
            )
        ).all()
    )


def waiting_entries_with_live_work(session: Session) -> list[Violation]:
    """`entered_at` is unknown: an entry does not date its status changes."""
    return [
        Violation(
            subject_id=entry.id,
            ticket_id=entry.ticket_id,
            entered_at=None,
            detail=(
                f"lane {entry.slot_number} entry {entry.id} waits to start while the run it "
                "already started is live"
            ),
        )
        for entry in _waiting_with_live_work(session)
    ]


def reactivate_entries_with_live_work(session: Session, violations: Sequence[Violation]) -> int:
    """Back to ACTIVE, so its lane does not start it twice. Re-read here: the run
    may have ended since detection, and then the entry's wait is the truth."""
    wanted = {violation.subject_id for violation in violations}
    entries = [entry for entry in _waiting_with_live_work(session) if entry.id in wanted]
    for entry in entries:
        entry.status = QueuePosition.ACTIVE
        session.add(entry)
    session.commit()
    return len(entries)


WAITING_ENTRIES_HAVE_NO_LIVE_WORK = Invariant(
    name="waiting_entries_have_no_live_work",
    ticket="lg-queue-execution-110",
    statement="no queue entry waits to start while the run it already started is live",
    detect=waiting_entries_with_live_work,
    repair=reactivate_entries_with_live_work,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 23),
    prevented_by=(
        "server/tests/test_bulk_queue_operations.py::test_only_a_failed_entry_can_be_retried"
    ),
)


def _held_but_blocked(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=entry.repairing_since,
            detail=f"{ticket.external_id} reads blocked on '{ticket.workflow_stage_key}' while "
            f"lane {entry.slot_number} is held for its {entry.repair_route} repair",
        )
        for entry, ticket in held_but_blocked(session)
    ]


def _rearm_held(session: Session, violations: Sequence[Violation]) -> int:
    tickets = (session.get(Ticket, violation.subject_id) for violation in violations)
    return rearm_held(session, [ticket for ticket in tickets if ticket is not None])


HELD_REPAIRS_READ_IN_PROGRESS = Invariant(
    name="held_repairs_read_in_progress",
    ticket="lg-queue-execution-1656",
    statement=(
        "no ticket whose lane is held for a repair (other than an interruption) reads blocked"
    ),
    detect=_held_but_blocked,
    repair=_rearm_held,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 29),
    prevented_by="server/tests/test_lane_repair_hold.py::test_every_lane_hold_re_arms_its_stage",
)


SETTLED_HOLDS_READ_BLOCKED = Invariant(
    name="settled_holds_read_blocked",
    ticket="lg-milestone-that-1420",
    statement=(
        "no ticket whose lane hold ended without its repair reads in progress with nothing "
        "running it"
    ),
    detect=settled_hold_violations,
    repair=reblock_settled_violations,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 14),
    prevented_by=(
        "server/tests/test_held_repairs_settle_blocked.py::"
        "test_every_way_a_hold_settles_leaves_the_ticket_blocked"
    ),
)
