"""What a lane already ran: the read model `queued_runs` never had.

An entry leaves the board the moment its lane releases, and nothing deletes it —
it flips to a terminal `status` and keeps its failure reason, retry count and
timestamps. Until this module there was no way to read that back, so a ticket
that blocked mid-pipeline was only recoverable with hand-written SQL.

One trap this exists to defuse: in `QueuePosition`, `ACTIVE` is the running
state and **`STARTED` is the terminal "lane released" state** set by
`QueueLaneService.on_orchestration_complete`. An entry's own status therefore
says which state machine it exited through, not what happened to the ticket.
The outcome comes from the orchestration run it dispatched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    Ticket,
    Workspace,
)
from sqlmodel import Session, col, select

#: Entry statuses that mean "still on the board" — waiting in a lane or running
#: in one. Everything else is history.
LIVE_STATUSES = (
    QueuePosition.QUEUED,
    QueuePosition.SCHEDULED,
    QueuePosition.PROMOTED,
    QueuePosition.ACTIVE,
)

#: What a card says happened, independent of which status the entry exited
#: through. `unknown` is for an entry that ended without an orchestration run to
#: answer for it (removed before dispatch, or stranded by a restart).
OUTCOMES = ("succeeded", "blocked", "failed", "cancelled", "running", "unknown")

_ORCHESTRATION_OUTCOME = {
    OrchestrationRunStatus.SUCCEEDED: "succeeded",
    OrchestrationRunStatus.BLOCKED: "blocked",
    OrchestrationRunStatus.FAILED: "failed",
    OrchestrationRunStatus.CANCELLED: "cancelled",
    OrchestrationRunStatus.RUNNING: "running",
    OrchestrationRunStatus.QUEUED: "running",
}


@dataclass(frozen=True)
class QueueHistoryEntry:
    """One finished lane entry, as the card renders it."""

    entry_id: str
    workspace_id: str
    workspace_slug: str
    workspace_name: str
    slot_number: int
    entry_kind: str
    stage_key: str
    status: str
    outcome: str
    ticket_id: str
    ticket_external_id: str
    ticket_title: str
    ticket_state: str
    orchestration_run_id: str | None
    run_code: str
    last_stage_key: str
    failure_reason: str
    retry_count: int
    created_at: datetime | None
    promoted_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: int | None


def derive_outcome(entry: QueuedRun, orchestration: OrchestrationRun | None) -> str:
    """What happened to the ticket, not which status the entry exited through.

    The entry's own terminal statuses are authoritative only when they record a
    decision the queue made itself (cancelled, gave up retrying). Otherwise the
    orchestration it dispatched is the one that knows.
    """
    if entry.status == QueuePosition.CANCELLED:
        return "cancelled"
    if entry.status == QueuePosition.FAILED and orchestration is None:
        return "failed"
    if orchestration is None:
        return "unknown"
    return _ORCHESTRATION_OUTCOME.get(orchestration.status, "unknown")


def _duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if not started_at or not finished_at:
        return None
    return max(0, int((finished_at - started_at).total_seconds()))


class QueueHistoryService:
    """Read finished lane entries back, newest first."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_history(
        self,
        *,
        workspace_id: str = "",
        outcome: str = "",
        slot_number: int | None = None,
        ticket_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[QueueHistoryEntry], int]:
        """Finished entries plus the total matching count, before paging.

        `outcome` filters on the derived value, which no column holds, so it is
        applied after the join rather than in SQL.
        """
        stmt = select(QueuedRun, Ticket, OrchestrationRun, Workspace).where(
            col(QueuedRun.status).not_in(LIVE_STATUSES)
        )
        stmt = stmt.join(Ticket, col(QueuedRun.ticket_id) == col(Ticket.id))
        stmt = stmt.join(Workspace, col(QueuedRun.workspace_id) == col(Workspace.id))
        stmt = stmt.join(
            OrchestrationRun,
            col(QueuedRun.orchestration_run_id) == col(OrchestrationRun.id),
            isouter=True,
        )
        if workspace_id:
            stmt = stmt.where(QueuedRun.workspace_id == workspace_id)
        if slot_number is not None:
            stmt = stmt.where(QueuedRun.slot_number == slot_number)
        if ticket_id:
            stmt = stmt.where(QueuedRun.ticket_id == ticket_id)
        stmt = stmt.order_by(
            col(QueuedRun.started_at).desc(),
            col(QueuedRun.created_at).desc(),
        )

        rows = self.session.exec(stmt).all()
        entries = [
            _to_entry(entry, ticket, orchestration, workspace)
            for entry, ticket, orchestration, workspace in rows
        ]
        if outcome:
            entries = [item for item in entries if item.outcome == outcome]

        total = len(entries)
        return entries[offset : offset + limit], total


def _to_entry(
    entry: QueuedRun,
    ticket: Ticket,
    orchestration: OrchestrationRun | None,
    workspace: Workspace,
) -> QueueHistoryEntry:
    finished_at = orchestration.finished_at if orchestration else entry.last_failed_at
    return QueueHistoryEntry(
        entry_id=entry.id,
        workspace_id=entry.workspace_id,
        workspace_slug=workspace.slug,
        workspace_name=workspace.name,
        slot_number=entry.slot_number,
        entry_kind=entry.entry_kind,
        stage_key=entry.stage_key,
        status=entry.status.value,
        outcome=derive_outcome(entry, orchestration),
        ticket_id=ticket.id,
        ticket_external_id=ticket.external_id,
        ticket_title=ticket.title,
        ticket_state=ticket.state.value,
        orchestration_run_id=entry.orchestration_run_id,
        run_code=orchestration.run_code if orchestration else "",
        last_stage_key=(orchestration.current_stage_key if orchestration else entry.stage_key),
        failure_reason=entry.failure_reason
        or (orchestration.error_message if orchestration else ""),
        retry_count=entry.retry_count,
        created_at=entry.created_at,
        promoted_at=entry.promoted_at,
        started_at=entry.started_at,
        finished_at=finished_at,
        duration_seconds=_duration_seconds(entry.started_at, finished_at),
    )
