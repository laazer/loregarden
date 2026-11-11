"""Research questions, their candidate projects, and capability claims.

A research thread (a Home Baxter thread in ``research`` mode) answers a
capability question — "which repos generate a skeleton for an unrigged mesh?" —
with candidates, and for each candidate a claim per capability the question
needs. A claim says how far it was checked (``ClaimStatus``); only a
``verified`` one names a file at a commit, and the service refuses a
``verified`` claim whose file is not in the clone it says it read.

Claims are keyed by the project's canonical URL, not by a library source,
because most candidates are never queued: a person's "it can't do that" must
still be on the record the next time the project comes up.

Re-exported from ``models.domain``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from loregarden.models.domain.survey_enums import ClaimRecorder, ClaimStatus
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

__all__ = [
    "SurveyCapabilityClaim",
    "SurveyResearchCandidate",
    "SurveyResearchQuestion",
    "SurveySourceResearch",
]


def _id() -> str:
    return str(uuid4())


class SurveyResearchQuestion(SQLModel, table=True):
    __tablename__ = "survey_research_questions"

    id: str = Field(default_factory=_id, primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    #: The research thread it was asked in.
    thread_id: str = Field(foreign_key="baxter_chat_sessions.id", index=True)
    #: The project area it is pinned to, once saved; None while ad hoc.
    area_id: str | None = Field(default=None, foreign_key="survey_areas.id", index=True)
    question: str
    #: JSON list of the capabilities a candidate must have, in the asker's words.
    capabilities_json: str = "[]"
    #: Days between automatic re-runs; None re-runs only when asked.
    interval_days: int | None = None
    #: Bumped each time candidates are recorded; candidates remember theirs.
    run_count: int = 0
    last_run_at: datetime | None = None
    #: A saved question is listed in the library; an ad-hoc one only in its thread.
    saved: bool = False
    archived_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SurveyResearchCandidate(SQLModel, table=True):
    __tablename__ = "survey_research_candidates"
    __table_args__ = (UniqueConstraint("question_id", "url", name="uq_survey_research_candidate"),)

    id: str = Field(default_factory=_id, primary_key=True)
    question_id: str = Field(foreign_key="survey_research_questions.id", index=True)
    #: Canonical project URL (a repository's one spelling, see `queue.canonical_url`).
    url: str = Field(index=True)
    name: str
    #: Why the agent put it forward, in a sentence.
    summary: str = ""
    #: The library source for this URL, when there is one.
    source_id: str | None = Field(default=None, foreign_key="survey_sources.id", index=True)
    licence_name: str = ""
    language: str = ""
    stars: int | None = None
    last_commit_at: datetime | None = None
    #: Why the metadata above is missing, when it is; "" when it was read.
    metadata_error: str = ""
    first_seen_run: int = 0
    last_seen_run: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SurveyCapabilityClaim(SQLModel, table=True):
    __tablename__ = "survey_capability_claims"

    id: str = Field(default_factory=_id, primary_key=True)
    url: str = Field(index=True)
    source_id: str | None = Field(default=None, foreign_key="survey_sources.id", index=True)
    capability: str
    status: ClaimStatus = Field(sa_column=str_enum_column(ClaimStatus, ClaimStatus.DOCS_ONLY))
    #: Path inside the repository, for a claim read in its source.
    evidence_path: str = ""
    #: The commit that path was read at.
    evidence_commit: str = ""
    #: The cited lines (1-based, inclusive); 0 when the claim cites none.
    evidence_start: int = 0
    evidence_end: int = 0
    #: Those lines as the file held them at the commit, read by the server.
    evidence_excerpt: str = ""
    #: A link a person can open: the file at the commit, or the doc page.
    evidence_url: str = ""
    note: str = ""
    recorded_by: ClaimRecorder = Field(
        sa_column=str_enum_column(ClaimRecorder, ClaimRecorder.AGENT)
    )
    #: The agent run that recorded it; "" for a person's correction.
    run_id: str = ""
    question_id: str | None = Field(
        default=None, foreign_key="survey_research_questions.id", index=True
    )
    #: The later claim about the same URL and capability that replaced this one.
    superseded_by: str | None = Field(default=None, foreign_key="survey_capability_claims.id")
    created_at: datetime = Field(default_factory=utcnow)


class SurveySourceResearch(SQLModel, table=True):
    """The research question a queued library source came from."""

    __tablename__ = "survey_source_research"

    source_id: str = Field(foreign_key="survey_sources.id", primary_key=True)
    question_id: str = Field(foreign_key="survey_research_questions.id", primary_key=True)
    added_at: datetime = Field(default_factory=utcnow)
