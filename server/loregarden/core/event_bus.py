import json
import threading
from collections.abc import Callable
from typing import Any

from loregarden.models.domain import DomainEvent, EventType
from sqlmodel import Session, select


class EventBus:
    """In-process event bus. Persists events and notifies subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[[DomainEvent], None]] = []

    def subscribe(self, handler: Callable[[DomainEvent], None]) -> None:
        self._subscribers.append(handler)

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
            persisted = session.get(DomainEvent, event.id)
            event = persisted or event
            for handler in self._subscribers:
                handler(event)
            return event

    def list_recent(self, session: Session, *, limit: int = 100) -> list[DomainEvent]:
        return list(
            session.exec(
                select(DomainEvent).order_by(DomainEvent.created_at.desc()).limit(limit)
            ).all()
        )


event_bus = EventBus()
_publish_lock = threading.Lock()
