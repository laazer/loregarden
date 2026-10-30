"""Filing records onto a conversation's living map, and reading it back.

Filing is idempotent and append-stable:

- a record already on the map (same `source_ref`) keeps its item and its
  `seq`; only its state and text follow the record;
- a record new to the map is appended, so it takes the next `seq` and every
  item already drawn stays where it is. New records filed together go in the
  order they happened, a topic just before the first item that hangs off it.

A record that later disappears leaves its item: the map is a history of what
was seen, not a mirror of what exists now.
"""

from __future__ import annotations

import threading
from datetime import datetime

from loregarden.models.domain import (
    BaxterChatSession,
    LivingMapItem,
    MapItemKind,
    MapItemSource,
    MapItemState,
    MapScope,
    Ticket,
)
from loregarden.models.domain.enums import comparable_utc, utcnow
from loregarden.services.living_map.records import (
    MapRecord,
    RecordRead,
    SourceReport,
    chat_records,
    ticket_records,
)
from loregarden.services.ticket_ids import resolve_one
from pydantic import BaseModel
from sqlmodel import Session, col, select

#: One filer at a time: two readers of the same map would otherwise hand out
#: the same next `seq` (the unique constraint would refuse the second).
_FILE_LOCK = threading.Lock()


class MapItemView(BaseModel):
    id: str
    seq: int
    kind: MapItemKind
    state: MapItemState
    source: MapItemSource
    title: str
    detail: str
    topic_id: str | None
    happened_at: datetime


class LivingMapView(BaseModel):
    scope: MapScope
    scope_id: str
    title: str
    items: list[MapItemView]
    #: Every source read, with its count or why it could not be read.
    sources: list[SourceReport]


class MapNotFound(LookupError):
    """The chat or ticket the map was asked for does not exist."""


def living_map(session: Session, scope: MapScope, ref: str) -> LivingMapView:
    """File what the records say now, then return the whole map in vine order."""
    scope_id, title, read = _read(session, scope, ref)
    with _FILE_LOCK:
        _file(session, scope, scope_id, read.records)
    items = session.exec(
        select(LivingMapItem)
        .where(LivingMapItem.scope == scope, LivingMapItem.scope_id == scope_id)
        .order_by(col(LivingMapItem.seq))
    ).all()
    return LivingMapView(
        scope=scope,
        scope_id=scope_id,
        title=title,
        items=[MapItemView.model_validate(item, from_attributes=True) for item in items],
        sources=read.sources,
    )


def _read(session: Session, scope: MapScope, ref: str) -> tuple[str, str, RecordRead]:
    if scope == MapScope.CHAT:
        chat = session.get(BaxterChatSession, ref)
        if chat is None:
            raise MapNotFound(f"No chat {ref}")
        return chat.id, chat.title, chat_records(session, chat.id)
    ticket = session.get(Ticket, ref)
    if ticket is None:
        try:
            ticket = resolve_one(session, ref)
        except ValueError as exc:
            raise MapNotFound(str(exc)) from exc
    return ticket.id, ticket.title, ticket_records(session, ticket)


def _filing_order(record: MapRecord) -> tuple[datetime, int, str]:
    # A topic first among records that happened at the same moment: it is
    # stamped with the moment its first item happened.
    return (
        comparable_utc(record.happened_at),
        0 if record.kind == MapItemKind.TOPIC else 1,
        record.source_ref,
    )


def _file(session: Session, scope: MapScope, scope_id: str, records: list[MapRecord]) -> None:
    items = {
        item.source_ref: item
        for item in session.exec(
            select(LivingMapItem).where(
                LivingMapItem.scope == scope, LivingMapItem.scope_id == scope_id
            )
        )
    }
    next_seq = max((item.seq for item in items.values()), default=0) + 1
    now = utcnow()
    for record in sorted(records, key=_filing_order):
        item = items.get(record.source_ref)
        if item is None:
            item = LivingMapItem(
                scope=scope,
                scope_id=scope_id,
                seq=next_seq,
                kind=record.kind,
                state=record.state,
                source=MapItemSource.RECORD,
                source_ref=record.source_ref,
                title=record.title,
                detail=record.detail,
                happened_at=record.happened_at,
            )
            next_seq += 1
            items[record.source_ref] = item
            session.add(item)
        elif (item.kind, item.state, item.title, item.detail) != (
            record.kind,
            record.state,
            record.title,
            record.detail,
        ):
            item.kind = record.kind
            item.state = record.state
            item.title = record.title
            item.detail = record.detail
            item.updated_at = now
    session.flush()
    for record in records:
        topic = items.get(record.topic_ref) if record.topic_ref else None
        item = items[record.source_ref]
        topic_id = topic.id if topic is not None else None
        if item.topic_id != topic_id:
            item.topic_id = topic_id
    session.commit()
