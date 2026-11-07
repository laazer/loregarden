"""Findings an agent run filed about a ticket's work, and the scope decision on each.

lg-milestone-that-1216 was a milestone whose fan-out never ended: its review
lanes filed findings as new tickets, those tickets landed as its children, its
child pass ran them, their review lanes filed more, and 37 run-filed tickets
accumulated under one root.

The filing itself was not the defect. Nothing decided whether a finding was
part of the ticket's work. The reviewer who noticed it filed it, and the only
outcome a filing had was "new ticket". So each filing writes one directed row
here, *undecided* until the ticket's spec owner makes the scope decision
(`services.scope_round`):

- ``AMEND``: it was this ticket's work. It becomes an acceptance criterion of
  the source ticket, and the finding is closed into it.
- ``FOLLOW_UP``: real work beyond this ticket's criteria that its parent owns.
  The parent waits for it.
- ``DEFER``: real, but not this tree's work. It stays where it was filed and
  no parent waits for it.
- ``DROP``: not worth doing. The ticket is closed ``wont_do``.

Until it is decided, a finding is held out of its parent's work like a
deferred one, and its source ticket cannot reach its gate.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from sqlmodel import Field, SQLModel


class FindingDecision(str, Enum):
    """The spec owner's scope decision on one finding."""

    AMEND = "amend"
    FOLLOW_UP = "follow_up"
    DEFER = "defer"
    DROP = "drop"


class TicketDiscovery(SQLModel, table=True):
    __tablename__ = "ticket_discoveries"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    #: The ticket the run filed. A ticket is filed once, so this is unique.
    filed_ticket_id: str = Field(foreign_key="tickets.id", unique=True)
    #: The ticket the run was working on when it filed it: whose scope it questions.
    source_ticket_id: str = Field(foreign_key="tickets.id", index=True)
    agent_run_id: str = Field(foreign_key="agent_runs.id", index=True)
    #: The drive the filing run belonged to, when it had one: the per-drive cap
    #: counts on it.
    orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id", index=True
    )
    filed_at: datetime = Field(default_factory=utcnow)
    #: Null while undecided.
    decision: FindingDecision | None = Field(
        default=None, sa_column=str_enum_column(FindingDecision, None, nullable=True, index=True)
    )
    decided_at: datetime | None = None
    #: The spec run that decided it, or ``operator`` / ``backfill``.
    decided_by: str = ""
    #: Why, in the decider's words. Required for every decision.
    decision_reason: str = ""


__all__ = ["FindingDecision", "TicketDiscovery"]
