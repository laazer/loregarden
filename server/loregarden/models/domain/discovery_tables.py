"""Work an agent run discovered and filed, kept apart until someone triages it.

lg-milestone-that-1216 was a milestone whose fan-out never ended: its review
lanes filed findings as new tickets, those tickets landed as its children, its
child pass ran them, their review lanes filed more, and 37 run-filed tickets
accumulated under one root. The parent could not finish until every one of
them did.

A ticket a run files is a *finding about* the work, not part of it. That is
beads' ``discovered-from`` edge (steveyegge/beads) and an SRE postmortem's
action items: recorded with where they came from, and never holding the
incident open. So each filing writes one directed row here. While
``triaged_at`` is null the ticket is *untriaged*: it stays where it was filed,
no parent waits for it and no dispatcher starts it, until triage promotes it
into the work or closes it (`services.discovered_work`).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from loregarden.models.domain.enums import utcnow
from sqlmodel import Field, SQLModel


class DiscoveryTriage(str, Enum):
    """What triage decided about a run's finding."""

    #: It is its parent's work from here on.
    PROMOTE = "promote"
    #: It is not work; the ticket is closed ``wont_do``.
    CLOSE = "close"


class TicketDiscovery(SQLModel, table=True):
    __tablename__ = "ticket_discoveries"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    #: The ticket the run filed. A ticket is filed once, so this is unique.
    filed_ticket_id: str = Field(foreign_key="tickets.id", unique=True)
    #: The ticket the run was working on when it filed it.
    source_ticket_id: str = Field(foreign_key="tickets.id", index=True)
    agent_run_id: str = Field(foreign_key="agent_runs.id", index=True)
    #: The drive the filing run belonged to, when it had one: the per-drive cap
    #: counts on it.
    orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id", index=True
    )
    filed_at: datetime = Field(default_factory=utcnow)
    #: Null while untriaged. Set when triage promotes or closes the ticket.
    triaged_at: datetime | None = Field(default=None, index=True)
    #: Who triaged it, and how: ``"<actor>: promote"`` / ``"<actor>: close"``.
    triaged_by: str = ""


__all__ = ["DiscoveryTriage", "TicketDiscovery"]
