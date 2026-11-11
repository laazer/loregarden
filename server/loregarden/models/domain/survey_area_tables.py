"""Project areas a reference can be read for, finer than a workspace.

A workspace is a whole product; a reference usually serves one part of it: an
observability tool serves loremaker's operations and loregarden's capacity
ledger, not either product at large. `SurveyArea` is one such part, named the
way its team names it (loremaker's code names: stratos, xeditor, dexter, …),
with a line saying what it is and words a reference about it would use.
`SurveySourceArea` links a source to the areas it serves; a pass reads for
them, and a finding no ticket covers is filed under the area's home ticket.

Areas live in the database and are edited from the reference library; the
migration that made them seeded each known workspace's areas once.

Re-exported from ``models.domain``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import utcnow
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

__all__ = ["SurveyArea", "SurveySourceArea"]


def _id() -> str:
    return str(uuid4())


class SurveyArea(SQLModel, table=True):
    __tablename__ = "survey_areas"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_survey_area_slug"),)

    id: str = Field(default_factory=_id, primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    #: Unique within the workspace; labels read `<workspace>/<slug>`.
    slug: str
    name: str
    #: What this part of the product is, in a line or two; the mapping agent reads it.
    description: str = ""
    #: JSON list of words a reference about this area would use.
    keywords_json: str = "[]"
    #: Where a finding for this area that no ticket covers is filed; None files it
    #: under the workspace's findings home, as an untagged source's would be.
    home_ticket_id: str | None = Field(default=None, foreign_key="tickets.id")
    archived_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SurveySourceArea(SQLModel, table=True):
    __tablename__ = "survey_source_areas"

    source_id: str = Field(foreign_key="survey_sources.id", primary_key=True)
    area_id: str = Field(foreign_key="survey_areas.id", primary_key=True, index=True)
    #: Why the person or the mapping agent tied them; "" when nobody said.
    reason: str = ""
    added_at: datetime = Field(default_factory=utcnow)
