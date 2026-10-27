"""The living map: what waits on the operator, what is open and what was settled.

Re-exported from ``models.domain``. One table serves every scope: a Baxter chat
or a ticket. A row is one item on that conversation's map, filed from a record
loregarden already keeps (`services.living_map`), and placed on the vine by its
``seq``: handed out once, in the order items appear, and never changed. That is
what keeps the drawing append-stable: a new item only extends the tip.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class MapScope(StrEnum):
    """Whose map an item is on."""

    CHAT = "chat"
    TICKET = "ticket"


class MapItemKind(StrEnum):
    """What an item is. A topic is not an item of work: it is what the items
    around it are about, drawn as a tendril tying them together."""

    TOPIC = "topic"
    QUESTION = "question"
    TASK = "task"
    #: The run is stopped on a person: a gate, a hand-off, a rework pause.
    WAIT = "wait"
    #: Something an agent assumed and recorded (a checkpoint).
    ASSUMPTION = "assumption"
    #: Work that happened: a stage run finished, failed or was cancelled.
    CHANGE = "change"


class MapItemState(StrEnum):
    OPEN = "open"
    #: On the operator: answer, approve or act.
    WAITING = "waiting"
    #: Answered, decided, done, recorded.
    SETTLED = "settled"
    DROPPED = "dropped"
    FAILED = "failed"


class MapItemSource(StrEnum):
    """Who filed an item. Model extraction (lg-living-map-1621) adds its own."""

    RECORD = "record"


class LivingMapItem(SQLModel, table=True):
    __tablename__ = "living_map_items"
    __table_args__ = (
        # A record files one item per map, however often the map is rebuilt.
        UniqueConstraint("scope", "scope_id", "source_ref", name="uq_living_map_items_source"),
        UniqueConstraint("scope", "scope_id", "seq", name="uq_living_map_items_seq"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    scope: MapScope = Field(sa_column=str_enum_column(MapScope))
    #: The chat session's id, or the ticket's.
    scope_id: str = Field(index=True)
    #: Place on the vine, 1-based, handed out on append and never changed.
    seq: int
    kind: MapItemKind = Field(sa_column=str_enum_column(MapItemKind))
    state: MapItemState = Field(sa_column=str_enum_column(MapItemState))
    source: MapItemSource = Field(sa_column=str_enum_column(MapItemSource))
    #: The record this item was filed from, e.g. ``approval:<id>``.
    source_ref: str
    title: str
    detail: str = ""
    #: The topic item this one hangs off, when it has one.
    topic_id: str | None = Field(default=None, foreign_key="living_map_items.id")
    #: When the record says it happened; ordering for the first build only.
    happened_at: datetime
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
