"""Tables owned by Home Baxter chat beyond the thread and its messages.

Kept out of ``tables.py``, which sits near the module size cap. Re-exported
from ``models.domain``.

- ``BaxterChatFocus`` is one workspace or initiative a thread was asked to
  work across, on top of what its archive already implies.
- ``FeatureCriterion`` is one acceptance criterion in a thread's feature loop:
  proposed by Baxter, accepted or dropped by the operator, then carried onto
  the backing ticket a round at a time (see ``services.chat_feature_loop``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.chat_enums import ChatFocusKind, FeatureCriterionStatus
from loregarden.models.domain.enums import str_enum_column, utcnow
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class BaxterChatFocus(SQLModel, table=True):
    """One workspace or initiative a thread works across."""

    __tablename__ = "baxter_chat_focus"
    __table_args__ = (UniqueConstraint("session_id", "kind", "ref_id"),)

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="baxter_chat_sessions.id", index=True)
    kind: ChatFocusKind = Field(sa_column=str_enum_column(ChatFocusKind))
    #: A workspace id or an initiative (ticket) id, by ``kind``.
    ref_id: str
    created_at: datetime = Field(default_factory=utcnow)


class FeatureCriterion(SQLModel, table=True):
    """One acceptance criterion in a chat's feature loop."""

    __tablename__ = "feature_criteria"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="baxter_chat_sessions.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    text: str
    status: FeatureCriterionStatus = Field(
        sa_column=str_enum_column(
            FeatureCriterionStatus, FeatureCriterionStatus.PROPOSED, index=True
        )
    )
    #: Which round carried it onto the ticket; 0 until one does.
    round: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None
