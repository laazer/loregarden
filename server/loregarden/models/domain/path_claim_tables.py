"""Files an agent declared it is about to change (`loregarden_claim_paths`).

Only declarations are stored. Planned and observed claims are derived on read
from the plan artifact and the runs' `changed_paths_json` (`services.path_claims`),
and a declaration is the one source that exists nowhere else.

There is no release column: a claim is read only through its ticket while that
ticket is open and unlanded, so landing or closing the ticket is the release.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import utcnow
from sqlmodel import Field, SQLModel


class DeclaredPathClaim(SQLModel, table=True):
    __tablename__ = "path_claims"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    #: The run that declared it, when the call named one of the ticket's runs.
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    #: Repo-relative; a trailing ``/`` claims a directory.
    path: str
    #: What the agent is about to do there, for the sibling that reads it.
    reason: str = ""
    created_at: datetime = Field(default_factory=utcnow)


__all__ = ["DeclaredPathClaim"]
