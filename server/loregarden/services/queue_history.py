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

A second trap: immediate admission (`reserve` + `bind`) and orphan-heal used to
claim a slot without creating a `QueuedRun`. Those runs finished on the ticket
but never appeared in history — so a later success after an interruption left
the board looking like the ticket had only failed. History also synthesizes
cards for those terminal orchestrations when no lane entry points at them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

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

#: Outcomes a lane keeps on its own card until someone acknowledges them. A
#: cancelled entry was a decision, not a surprise; these two were not.
ATTENTION_OUTCOMES = ("blocked", "failed")

#: Cards carried per lane in the queue snapshot. The websocket pushes that
#: snapshot every few seconds, so an unbounded list would ride along with it —
#: the count of what is held back travels instead.
MAX_ATTENTION_PER_LANE = 10

_ORCHESTRATION_OUTCOME = {
    OrchestrationRunStatus.SUCCEEDED: "succeeded",
    OrchestrationRunStatus.BLOCKED: "blocked",
    OrchestrationRunStatus.FAILED: "failed",
    OrchestrationRunStatus.CANCELLED: "cancelled",
    OrchestrationRunStatus.RUNNING: "running",
    OrchestrationRunStatus.QUEUED: "running",
}

_TERMINAL_ORCHESTRATION = (
    OrchestrationRunStatus.SUCCEEDED,
    OrchestrationRunStatus.BLOCKED,
    OrchestrationRunStatus.FAILED,
    OrchestrationRunStatus.CANCELLED,
)


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


def _history_sort_key(item: QueueHistoryEntry) -> datetime:
    ts = item.started_at or item.created_at
    if ts is None:
        return datetime.min.replace(tzinfo=None)
    return ts.replace(tzinfo=None) if ts.tzinfo else ts


def _ranges_overlap(
    start_a: datetime | None,
    end_a: datetime | None,
    start_b: datetime | None,
    end_b: datetime | None,
) -> bool:
    if not start_a or not start_b:
        return False

    def _naive(value: datetime) -> datetime:
        return value.replace(tzinfo=None) if value.tzinfo else value

    start_a = _naive(start_a)
    start_b = _naive(start_b)
    # Open-ended ranges still occupy time from start onward.
    end_a = _naive(end_a) if end_a else datetime.max
    end_b = _naive(end_b) if end_b else datetime.max
    return start_a <= end_b and start_b <= end_a


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
        entries.extend(
            self._synthetic_direct_admissions(workspace_id=workspace_id, ticket_id=ticket_id)
        )
        entries.sort(key=_history_sort_key, reverse=True)
        if slot_number is not None:
            entries = [item for item in entries if item.slot_number == slot_number]
        if outcome:
            entries = [item for item in entries if item.outcome == outcome]

        total = len(entries)
        return entries[offset : offset + limit], total

    def lane_attention(self) -> dict[int, tuple[list[QueueHistoryEntry], int]]:
        """Per lane: what blocked or failed in it and has not been acknowledged.

        Only real lane entries — a synthetic card stands for an orchestration
        that never held a lane entry, so there is no lane to pin it to and
        nothing to dismiss it with. Those stay in the history rail.

        Returns ``{slot_number: (cards, total)}`` where ``total`` counts every
        undismissed entry for that lane, including any beyond the cap.
        """
        stmt = (
            select(QueuedRun, Ticket, OrchestrationRun, Workspace)
            .where(col(QueuedRun.status).not_in(LIVE_STATUSES))
            .where(col(QueuedRun.dismissed_at).is_(None))
            .where(QueuedRun.slot_number > 0)
            .join(Ticket, col(QueuedRun.ticket_id) == col(Ticket.id))
            .join(Workspace, col(QueuedRun.workspace_id) == col(Workspace.id))
            .join(
                OrchestrationRun,
                col(QueuedRun.orchestration_run_id) == col(OrchestrationRun.id),
                isouter=True,
            )
        )
        cards = [
            _to_entry(entry, ticket, orchestration, workspace)
            for entry, ticket, orchestration, workspace in self.session.exec(stmt).all()
            if derive_outcome(entry, orchestration) in ATTENTION_OUTCOMES
        ]
        cards.sort(key=_history_sort_key, reverse=True)

        by_lane: dict[int, list[QueueHistoryEntry]] = {}
        for card in cards:
            by_lane.setdefault(card.slot_number, []).append(card)
        return {
            slot_number: (lane_cards[:MAX_ATTENTION_PER_LANE], len(lane_cards))
            for slot_number, lane_cards in by_lane.items()
        }

    def dismiss_entry(self, entry_id: str) -> bool:
        """Acknowledge one blocked/failed entry so its lane stops showing it.

        Only a finished entry can be dismissed: doing this to a live one would
        hide something the lane is still working on, and the card it belongs to
        is not this section at all.
        """
        entry = self.session.get(QueuedRun, entry_id)
        if not entry or entry.status in LIVE_STATUSES:
            return False
        if entry.dismissed_at is None:
            entry.dismissed_at = datetime.now(timezone.utc)
            self.session.add(entry)
            self.session.commit()
        return True

    def _synthetic_direct_admissions(
        self, *, workspace_id: str = "", ticket_id: str = ""
    ) -> list[QueueHistoryEntry]:
        """Terminal orchestrations that never got a ``QueuedRun``.

        Nested child execute under a parent orchestration is skipped — the
        parent's lane entry already covers that tree. Standalone admits that
        reserved a slot without an entry still need a card, or a later success
        is invisible next to an earlier interruption failure.
        """
        linked = {
            orch_id
            for orch_id in self.session.exec(
                select(QueuedRun.orchestration_run_id).where(
                    col(QueuedRun.orchestration_run_id).is_not(None)
                )
            ).all()
            if orch_id
        }
        stmt = select(OrchestrationRun).where(
            col(OrchestrationRun.status).in_(_TERMINAL_ORCHESTRATION)
        )
        if workspace_id:
            stmt = stmt.where(OrchestrationRun.workspace_id == workspace_id)
        if ticket_id:
            stmt = stmt.where(OrchestrationRun.ticket_id == ticket_id)
        candidates = [orch for orch in self.session.exec(stmt).all() if orch.id not in linked]
        if not candidates:
            return []

        ticket_ids = {orch.ticket_id for orch in candidates}
        tickets = {
            ticket.id: ticket
            for ticket in self.session.exec(
                select(Ticket).where(col(Ticket.id).in_(ticket_ids))
            ).all()
        }
        # Ancestors may not be in candidates; load them for the nest check.
        pending = {t.parent_ticket_id for t in tickets.values() if t.parent_ticket_id}
        while pending:
            rows = self.session.exec(select(Ticket).where(col(Ticket.id).in_(pending))).all()
            pending = set()
            for ticket in rows:
                if ticket.id in tickets:
                    continue
                tickets[ticket.id] = ticket
                if ticket.parent_ticket_id and ticket.parent_ticket_id not in tickets:
                    pending.add(ticket.parent_ticket_id)

        ancestor_ids = {
            ticket.parent_ticket_id for ticket in tickets.values() if ticket.parent_ticket_id
        }
        parent_orchs: dict[str, list[OrchestrationRun]] = {}
        if ancestor_ids:
            for orch in self.session.exec(
                select(OrchestrationRun).where(col(OrchestrationRun.ticket_id).in_(ancestor_ids))
            ).all():
                parent_orchs.setdefault(orch.ticket_id, []).append(orch)

        workspace_ids = {orch.workspace_id for orch in candidates}
        workspaces = {
            ws.id: ws
            for ws in self.session.exec(
                select(Workspace).where(col(Workspace.id).in_(workspace_ids))
            ).all()
        }

        synthetic: list[QueueHistoryEntry] = []
        for orch in candidates:
            if self._nested_under_overlapping_parent(orch, tickets, parent_orchs):
                continue
            ticket = tickets.get(orch.ticket_id)
            workspace = workspaces.get(orch.workspace_id)
            if not ticket or not workspace:
                continue
            synthetic.append(_to_synthetic_entry(orch, ticket, workspace))
        return synthetic

    @staticmethod
    def _nested_under_overlapping_parent(
        orch: OrchestrationRun,
        tickets: dict[str, Ticket],
        parent_orchs: dict[str, list[OrchestrationRun]],
    ) -> bool:
        ticket = tickets.get(orch.ticket_id)
        seen: set[str] = set()
        while ticket and ticket.parent_ticket_id and ticket.parent_ticket_id not in seen:
            parent_id = ticket.parent_ticket_id
            seen.add(parent_id)
            for parent_orch in parent_orchs.get(parent_id, []):
                if _ranges_overlap(
                    orch.started_at,
                    orch.finished_at,
                    parent_orch.started_at,
                    parent_orch.finished_at,
                ):
                    return True
            ticket = tickets.get(parent_id)
        return False


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


def _to_synthetic_entry(
    orchestration: OrchestrationRun,
    ticket: Ticket,
    workspace: Workspace,
) -> QueueHistoryEntry:
    """A history card for a finished orchestration that never had a lane entry."""
    return QueueHistoryEntry(
        entry_id=f"orch:{orchestration.id}",
        workspace_id=orchestration.workspace_id,
        workspace_slug=workspace.slug,
        workspace_name=workspace.name,
        slot_number=0,
        entry_kind="orchestration",
        stage_key=orchestration.current_stage_key or "",
        status=QueuePosition.STARTED.value,
        outcome=_ORCHESTRATION_OUTCOME.get(orchestration.status, "unknown"),
        ticket_id=ticket.id,
        ticket_external_id=ticket.external_id,
        ticket_title=ticket.title,
        ticket_state=ticket.state.value,
        orchestration_run_id=orchestration.id,
        run_code=orchestration.run_code,
        last_stage_key=orchestration.current_stage_key or "",
        failure_reason=orchestration.error_message or "",
        retry_count=0,
        created_at=orchestration.started_at,
        promoted_at=orchestration.started_at,
        started_at=orchestration.started_at,
        finished_at=orchestration.finished_at,
        duration_seconds=_duration_seconds(orchestration.started_at, orchestration.finished_at),
    )
