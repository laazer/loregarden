"""The domain event log: how a ticket got to where it is.

DECIDED 2026-09-04 (lg-workflow-integrity-567), after two months of the table
being written by eight services and read by nothing.

**It is a queryable audit trail, and the writes stay.** The deciding measurement:
`domain_events` is the only table in the database that records TRANSITIONS.
`tickets.state` holds the current state and a revision counter, not a log; a
stage's status lives in `stages_json` and is overwritten in place. So
`TicketStateChanged` (427 rows), `StageStarted`, `StageCompleted` and
`StageSkipped` are not derivable from anywhere else — deleting them would destroy
the only record of how anything got where it is.

What was NOT kept is the subscription model. `EventBus.subscribe` had zero
callers in two months, so the handler list and the fan-out inside `publish` were
dead code advertising a capability nothing used. Removed rather than left
implying that publishing notifies something.

What made it unusable was never the writes — it was that `list_recent` took no
filters, so the only available question was "the last N events across the entire
installation". It takes filters now, backed by indexes (migration 0110).

Note the events whose content IS derivable elsewhere — `ArtifactCreated`,
`AgentRunStarted/Completed`, `TicketCreated`, `ApprovalRequested/Resolved`,
`OrchestrationRunStarted/Completed` — are deliberately still written. They cost
one row, and a log with holes in it is harder to reason about than a complete
one. `TRANSITION_EVENTS` names the subset nothing else records.
"""

import json
import threading
from datetime import datetime
from typing import Any

from loregarden.models.domain import DomainEvent, EventType
from sqlmodel import Session, col, select

#: The events no other table records. A reader asking "how did this ticket get
#: here" wants these and not the eight kinds it could reconstruct from the rows
#: they describe.
TRANSITION_EVENTS: tuple[EventType, ...] = (
    EventType.TICKET_STATE_CHANGED,
    EventType.STAGE_STARTED,
    EventType.STAGE_COMPLETED,
    EventType.STAGE_SKIPPED,
)


class EventBus:
    """Persists domain events. Publishing notifies nothing — see the module docstring."""

    def publish(
        self,
        session: Session,
        event_type: EventType,
        *,
        workspace_id: str | None = None,
        ticket_id: str | None = None,
        run_id: str | None = None,
        artifact_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> DomainEvent:
        with _publish_lock:
            event = DomainEvent(
                type=event_type,
                workspace_id=workspace_id,
                ticket_id=ticket_id,
                run_id=run_id,
                artifact_id=artifact_id,
                payload_json=json.dumps(payload or {}),
            )
            session.add(event)
            session.commit()
            return session.get(DomainEvent, event.id) or event

    def list_recent(
        self,
        session: Session,
        *,
        limit: int = 100,
        ticket_id: str | None = None,
        workspace_id: str | None = None,
        types: tuple[EventType, ...] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[DomainEvent]:
        """Newest first. Every filter is optional and they compose.

        Unfiltered this is still "the last N events installation-wide", which was
        the only question the log could answer for its first two months and the
        reason nothing consumed it.
        """
        statement = select(DomainEvent)
        if ticket_id:
            statement = statement.where(col(DomainEvent.ticket_id) == ticket_id)
        if workspace_id:
            statement = statement.where(col(DomainEvent.workspace_id) == workspace_id)
        if types:
            statement = statement.where(col(DomainEvent.type).in_(types))
        if since:
            statement = statement.where(col(DomainEvent.created_at) >= since)
        if until:
            statement = statement.where(col(DomainEvent.created_at) <= until)
        statement = statement.order_by(col(DomainEvent.created_at).desc()).limit(limit)
        return list(session.exec(statement).all())

    def ticket_history(
        self, session: Session, ticket_id: str, *, limit: int = 100
    ) -> list[DomainEvent]:
        """How this ticket got to its current state, oldest first.

        Restricted to `TRANSITION_EVENTS`: the other kinds describe rows the
        reader can already see, and mixing them in buries the four that are the
        only record of anything.
        """
        events = self.list_recent(
            session, limit=limit, ticket_id=ticket_id, types=TRANSITION_EVENTS
        )
        return sorted(events, key=lambda event: event.created_at)


event_bus = EventBus()
_publish_lock = threading.Lock()
