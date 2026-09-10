"""A queue snapshot must not count released lanes as running.

`lg-workflow-integrity-699`. `QueuePosition.STARTED` reads like a running state
and is the terminal "lane released" one — written by `on_orchestration_complete`,
by the blocked release, and by `_settle_finished_entries` as its *settled* value.
`services/queue_history.py` opens by naming that trap; `api/queue_persistence.py`
had not got the memo and counted STARTED as `active_count`, over every QueuedRun
for the workspace, unfiltered.

Live effect: a snapshot reported `active_count: 128` against zero running agents,
with the oldest counted entry released on 2026-08-05.

The ticket was first filed claiming those 128 rows were leaks a sweep could never
settle. They are the sweep's output. That correction is why these tests assert
the meaning of each status rather than its name.
"""

from __future__ import annotations

from loregarden.models.domain import (
    RUNNING_QUEUE_STATUSES,
    QueuedRun,
    QueuePosition,
    Ticket,
    Workspace,
)
from loregarden.services.queue_history import LIVE_STATUSES
from sqlmodel import Session, select


def _entry(session: Session, ws: Workspace, ticket_id: str, status: QueuePosition) -> QueuedRun:
    entry = QueuedRun(
        workspace_id=ws.id,
        ticket_id=ticket_id,
        position=1,
        status=status,
    )
    session.add(entry)
    session.commit()
    return entry


def _counts(session: Session, ws: Workspace) -> dict:
    """The stats block, computed the way the endpoint computes it."""
    current_runs = session.exec(select(QueuedRun).where(QueuedRun.workspace_id == ws.id)).all()
    return {
        "total_runs": len(current_runs),
        "active_count": sum(1 for r in current_runs if r.status in RUNNING_QUEUE_STATUSES),
        "queued_count": sum(1 for r in current_runs if r.status == QueuePosition.QUEUED),
        "failed_count": sum(1 for r in current_runs if r.status == QueuePosition.FAILED),
    }


def test_a_released_lane_is_not_counted_as_active(db_session: Session, client):
    """The defect. STARTED means the lane was given back."""
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ticket = db_session.exec(select(Ticket)).first()

    for _ in range(3):
        _entry(db_session, ws, ticket.id, QueuePosition.STARTED)

    assert _counts(db_session, ws)["active_count"] == 0


def test_a_running_entry_is_counted(db_session: Session, client):
    """The other half: the fix must not simply report zero forever."""
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ticket = db_session.exec(select(Ticket)).first()

    _entry(db_session, ws, ticket.id, QueuePosition.ACTIVE)
    _entry(db_session, ws, ticket.id, QueuePosition.PROMOTED)
    _entry(db_session, ws, ticket.id, QueuePosition.STARTED)

    counts = _counts(db_session, ws)
    assert counts["active_count"] == 2
    assert counts["total_runs"] == 3


def test_failed_is_counted_by_the_enum_member(db_session: Session, client):
    """It compared against the bare string "failed", which works only because
    QueuePosition is a StrEnum — the stringly-typed comparison the organization
    gate exists to stop."""
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ticket = db_session.exec(select(Ticket)).first()

    _entry(db_session, ws, ticket.id, QueuePosition.FAILED)

    assert _counts(db_session, ws)["failed_count"] == 1


def test_started_is_never_a_running_status():
    """The regression pin, stated as the fact that was misread.

    Named explicitly so the next reader meets the trap in a failure message
    rather than in production: STARTED is terminal, however it reads.
    """
    assert QueuePosition.STARTED not in RUNNING_QUEUE_STATUSES
    assert set(RUNNING_QUEUE_STATUSES) == {QueuePosition.PROMOTED, QueuePosition.ACTIVE}


def test_every_queue_status_is_classified():
    """AC3: a member added later cannot be silently uncovered.

    Every QueuePosition must be knowingly running, waiting, or finished. Adding
    one without deciding which fails here, which is exactly the gap that let
    STARTED be read as running by one module and terminal by another.
    """
    waiting = {QueuePosition.QUEUED, QueuePosition.SCHEDULED, QueuePosition.REPAIRING}
    running = set(RUNNING_QUEUE_STATUSES)
    finished = {
        QueuePosition.STARTED,
        QueuePosition.CANCELLED,
        QueuePosition.FAILED,
        QueuePosition.SKIPPED,
        QueuePosition.PAUSED,
    }

    unclassified = set(QueuePosition) - waiting - running - finished
    assert not unclassified, (
        f"unclassified QueuePosition member(s): {sorted(m.value for m in unclassified)}. "
        "Decide whether each is running, waiting or finished, and update both this "
        "test and every reader — STARTED was misread precisely because nothing forced it."
    )
    # The two service-level sets must stay consistent with that classification.
    assert running <= set(LIVE_STATUSES), "a running entry must also be on the board"
    assert QueuePosition.STARTED not in LIVE_STATUSES
