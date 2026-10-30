"""The ship probe: which done tickets have a ship that is not green, as of one ask.

A done ticket's ship is the pull request carrying its tree's work. Nothing in
the stage map knows whether that PR is red, so `services.done_ticket_ship`
asks GitHub on the reconcile timer and records the answer here.

Why a table rather than findings alone: findings are upserted and never
deleted, so "GitHub said every ship is green" and "GitHub could not be asked"
would both read as an empty set. `listing_state` is the difference, and it is
the reason Home can present a list it did not have to re-ask for.

One current row per workspace, replaced each pass — not a history table.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import (
    CIStatus,
    PrListingState,
    ShipReason,
    str_enum_column,
    utcnow,
)
from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class ShipViolation(BaseModel):
    """One done ticket whose ship is not green, and the evidence for saying so.

    ``pr_number``/``pr_url`` are None exactly when ``reason`` is
    ``never_reached_remote``. ``check_names`` holds the failing-or-pending
    *required* check names only. ``ci_run_result`` is the tree root's recorded
    CI status, evidence for a reader rather than part of the predicate.
    """

    ticket_id: str
    external_id: str
    title: str
    reason: ShipReason
    pr_number: int | None = None
    pr_url: str | None = None
    check_names: list[str] = Field(default_factory=list)
    carrying_branch: str = ""
    ci_run_result: CIStatus | None = None

    @property
    def detail(self) -> str:
        """The sentence a finding carries, for a person reading the backlog."""
        if self.pr_number is None:
            return f"{self.external_id}: {self.carrying_branch} never reached remote"
        names = ", ".join(self.check_names)
        said = f": {names}" if names else ""
        return f"{self.external_id}: PR #{self.pr_number} {self.reason.value}{said}"


class ShipProbeView(BaseModel):
    """One workspace's current probe, as Home and Work items read it."""

    workspace_id: str
    workspace_slug: str
    probed_at: datetime | None
    listing_state: PrListingState
    error: str | None
    violations: list[ShipViolation] = Field(default_factory=list)


class ShipProbe(SQLModel, table=True):
    """The persisted probe. ``violations_json`` is a list of `ShipViolation`."""

    __tablename__ = "ship_probes"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True, unique=True)
    probed_at: datetime = Field(default_factory=utcnow)
    listing_state: PrListingState = Field(
        default=PrListingState.UNAVAILABLE,
        sa_column=str_enum_column(PrListingState, PrListingState.UNAVAILABLE, index=True),
    )
    #: Why GitHub could not be asked, when `listing_state` is UNAVAILABLE.
    error: str = ""
    violations_json: str = "[]"

    def violations(self) -> list[ShipViolation]:
        return [ShipViolation.model_validate(row) for row in json.loads(self.violations_json)]

    def set_violations(self, violations: list[ShipViolation]) -> None:
        self.violations_json = json.dumps([v.model_dump() for v in violations])


__all__ = ["ShipProbe", "ShipProbeView", "ShipViolation"]
