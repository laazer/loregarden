"""The reference survey's record: what was queued, read, concluded, and left open.

Before this, the record was `scratch/ref_projects/SURVEY_QUEUE.md`, 5,108 gitignored
lines nothing could query. The shape here is set by its two richest entries,
openharness (23 passes and a prioritised unread list) and ThinkTerm (8 passes):

- `SurveySource` is one queued target, with its licence classification and why
  it is queued.
- `SurveyPass` is one bounded read of it, against one question.
- `SurveyCoverage` is the directory map with file counts; `SurveyPassRead` marks
  what each pass did with each entry, so "read 3 of 400 files" is a query.
- `SurveyEvidence` is what an operation established, with its corpus and whether
  that corpus is complete; `SurveyFinding` cites evidence, so a claim that reaches
  a ticket says what it rests on.
- `SurveyUnknown` is an open question with both supporting and contradicting
  evidence, closed only by observed evidence, under optimistic concurrency.
- `SurveyLegacySection` holds the scratch file's sections verbatim, so the
  import is lossless and checkable by hash.

Re-exported from ``models.domain``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from loregarden.models.domain.survey_enums import (
    LicenceBasis,
    LicenceKind,
    LicenceUse,
    SurveyEpistemic,
    SurveyEvidenceStance,
    SurveyFindingClaim,
    SurveyFindingStatus,
    SurveyPassMode,
    SurveyPassOutcome,
    SurveyPassStatus,
    SurveyReadState,
    SurveySourceKind,
    SurveySourceStatus,
    SurveyTicketAction,
    SurveyUnknownStatus,
)
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

__all__ = [
    "SurveyCoverage",
    "SurveyEvidence",
    "SurveyFinding",
    "SurveyFindingEvidence",
    "SurveyLegacySection",
    "SurveyPass",
    "SurveyPassRead",
    "SurveyPassTicket",
    "SurveySource",
    "SurveyUnknown",
    "SurveyUnknownEvidence",
]


def _id() -> str:
    return str(uuid4())


class SurveySource(SQLModel, table=True):
    __tablename__ = "survey_sources"
    __table_args__ = (UniqueConstraint("workspace_id", "url", name="ux_survey_sources_url"),)

    id: str = Field(default_factory=_id, primary_key=True)
    #: The workspace whose backlog the findings land on.
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    kind: SurveySourceKind = Field(sa_column=str_enum_column(SurveySourceKind))
    url: str
    name: str = ""
    #: What the source is meant to answer, and why it is in the queue.
    question: str = ""
    queued_reason: str = ""
    #: Lower runs first; ties go to the oldest.
    priority: int = 100
    status: SurveySourceStatus = Field(
        default=SurveySourceStatus.PROPOSED,
        sa_column=str_enum_column(SurveySourceStatus, SurveySourceStatus.PROPOSED, index=True),
    )
    #: The inbox card a person resolves to admit the source.
    approval_id: str | None = Field(default=None, foreign_key="approvals.id")
    licence_kind: LicenceKind | None = Field(
        default=None, sa_column=str_enum_column(LicenceKind, nullable=True)
    )
    licence_basis: LicenceBasis | None = Field(
        default=None, sa_column=str_enum_column(LicenceBasis, nullable=True)
    )
    licence_use: LicenceUse | None = Field(
        default=None, sa_column=str_enum_column(LicenceUse, nullable=True)
    )
    #: The identifier or title as read, e.g. "GPL-3.0-only".
    licence_name: str = ""
    #: The constraint in words, for whoever reads a finding from this source.
    licence_constraint: str = ""
    #: The commit (or fetch date, for a site) everything below was read at.
    pinned_commit: str = ""
    fetched_at: datetime | None = None
    #: The commit the directory map was taken at; "" until mapped.
    mapped_commit: str = ""
    refused_reason: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SurveyPass(SQLModel, table=True):
    __tablename__ = "survey_passes"
    __table_args__ = (UniqueConstraint("source_id", "ordinal", name="ux_survey_passes_ordinal"),)

    id: str = Field(default_factory=_id, primary_key=True)
    source_id: str = Field(foreign_key="survey_sources.id", index=True)
    ordinal: int
    #: The pass's name in the legacy record ("AK", "PASS 7"); "" for new passes.
    label: str = ""
    mode: SurveyPassMode = Field(sa_column=str_enum_column(SurveyPassMode))
    status: SurveyPassStatus = Field(
        default=SurveyPassStatus.QUEUED,
        sa_column=str_enum_column(SurveyPassStatus, SurveyPassStatus.QUEUED, index=True),
    )
    question: str = ""
    #: Map paths this pass is assigned. Bounding a pass by the map is what makes
    #: its coverage predictable rather than "whatever fit in one context".
    slice_json: str = "[]"
    file_budget: int = 0
    read_commit: str = ""
    outcome: SurveyPassOutcome | None = Field(
        default=None, sa_column=str_enum_column(SurveyPassOutcome, nullable=True)
    )
    outcome_note: str = ""
    #: Where the pass stopped and what remains, in its own words.
    stopped_at: str = ""
    refused_reason: str = ""
    carrier_ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    legacy_section_id: str | None = Field(default=None, foreign_key="survey_legacy_sections.id")
    #: The legacy pass text this row was parsed from, verbatim.
    legacy_text: str = ""
    queued_at: datetime = Field(default_factory=utcnow)
    dispatched_at: datetime | None = None
    finished_at: datetime | None = None


class SurveyCoverage(SQLModel, table=True):
    """One entry of a source's directory map: a directory, or a file named alone."""

    __tablename__ = "survey_coverage"
    __table_args__ = (UniqueConstraint("source_id", "path", name="ux_survey_coverage_path"),)

    id: str = Field(default_factory=_id, primary_key=True)
    source_id: str = Field(foreign_key="survey_sources.id", index=True)
    path: str
    #: Part of the directory map, whose leaves sum to the tree. False for a finer
    #: entry (a single file a pass read, an item of an imported unread list), which
    #: rolls up into the map leaf above it and is never counted twice.
    in_map: bool = False
    #: Observed at `mapped_commit`; None when the entry came from prose that gave none.
    file_count: int | None = None
    byte_count: int | None = None
    mapped_commit: str = ""
    #: Reading order for continuation passes; lower first, None unranked.
    priority: int | None = None
    #: Why it is worth reading, or what it is expected to answer.
    note: str = ""


class SurveyPassRead(SQLModel, table=True):
    __tablename__ = "survey_pass_reads"
    __table_args__ = (UniqueConstraint("pass_id", "coverage_id", name="ux_survey_pass_reads"),)

    id: str = Field(default_factory=_id, primary_key=True)
    pass_id: str = Field(foreign_key="survey_passes.id", index=True)
    coverage_id: str = Field(foreign_key="survey_coverage.id", index=True)
    state: SurveyReadState = Field(sa_column=str_enum_column(SurveyReadState))
    files_read: int = 0
    note: str = ""


class SurveyPassTicket(SQLModel, table=True):
    __tablename__ = "survey_pass_tickets"

    id: str = Field(default_factory=_id, primary_key=True)
    pass_id: str = Field(foreign_key="survey_passes.id", index=True)
    #: None only for a legacy reference that could not be resolved (`legacy_ref`).
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    action: SurveyTicketAction = Field(sa_column=str_enum_column(SurveyTicketAction))
    finding_id: str | None = Field(default=None, foreign_key="survey_findings.id")
    #: The ticket the finding was aimed at, when a split or a done ticket moved it.
    aimed_at_ticket_id: str | None = Field(default=None, foreign_key="tickets.id")
    criteria_added: int = 0
    #: Read back after the write and found byte-exact.
    verified: bool = False
    legacy_ref: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class SurveyEvidence(SQLModel, table=True):
    """What one operation established, and the limits of what it could establish."""

    __tablename__ = "survey_evidence"

    id: str = Field(default_factory=_id, primary_key=True)
    source_id: str = Field(foreign_key="survey_sources.id", index=True)
    pass_id: str | None = Field(default=None, foreign_key="survey_passes.id", index=True)
    epistemic: SurveyEpistemic = Field(sa_column=str_enum_column(SurveyEpistemic))
    #: What was done: "grep -rn 'visual' client/src", "read docs/agent-status.md".
    operation: str
    result: str = ""
    #: Who or what makes this authoritative: "the file at @ccf410c", "their README".
    authority: str = ""
    limitations: str = ""
    residual_unknowns: str = ""
    #: The paths the operation searched or read.
    corpus_json: str = "[]"
    #: Kept apart from paging: the corpus holds everything the question could be
    #: answered from. Exhausting a page set says nothing about this.
    corpus_complete: bool = False
    completeness_basis: str = ""
    #: The denominator: what was examined of how much.
    examined_count: int | None = None
    total_count: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class SurveyFinding(SQLModel, table=True):
    __tablename__ = "survey_findings"

    id: str = Field(default_factory=_id, primary_key=True)
    source_id: str = Field(foreign_key="survey_sources.id", index=True)
    pass_id: str | None = Field(default=None, foreign_key="survey_passes.id", index=True)
    claim: SurveyFindingClaim = Field(sa_column=str_enum_column(SurveyFindingClaim))
    epistemic: SurveyEpistemic = Field(sa_column=str_enum_column(SurveyEpistemic))
    statement: str
    status: SurveyFindingStatus = Field(
        default=SurveyFindingStatus.ACTIVE,
        sa_column=str_enum_column(SurveyFindingStatus, SurveyFindingStatus.ACTIVE, index=True),
    )
    retracted_reason: str = ""
    replaced_by_id: str | None = Field(default=None, foreign_key="survey_findings.id")
    retracted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class SurveyFindingEvidence(SQLModel, table=True):
    __tablename__ = "survey_finding_evidence"

    finding_id: str = Field(foreign_key="survey_findings.id", primary_key=True)
    evidence_id: str = Field(foreign_key="survey_evidence.id", primary_key=True)


class SurveyUnknown(SQLModel, table=True):
    __tablename__ = "survey_unknowns"

    id: str = Field(default_factory=_id, primary_key=True)
    source_id: str = Field(foreign_key="survey_sources.id", index=True)
    opened_by_pass_id: str | None = Field(default=None, foreign_key="survey_passes.id")
    question: str
    #: The authority or environment still required to settle it.
    authority_required: str = ""
    status: SurveyUnknownStatus = Field(
        default=SurveyUnknownStatus.OPEN,
        sa_column=str_enum_column(SurveyUnknownStatus, SurveyUnknownStatus.OPEN, index=True),
    )
    resolution: str = ""
    resolving_evidence_id: str | None = Field(default=None, foreign_key="survey_evidence.id")
    #: Bumped by every update; a writer holding an older one rereads.
    revision: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SurveyUnknownEvidence(SQLModel, table=True):
    __tablename__ = "survey_unknown_evidence"

    unknown_id: str = Field(foreign_key="survey_unknowns.id", primary_key=True)
    evidence_id: str = Field(foreign_key="survey_evidence.id", primary_key=True)
    stance: SurveyEvidenceStance = Field(sa_column=str_enum_column(SurveyEvidenceStance))


class SurveyLegacySection(SQLModel, table=True):
    """One section of a legacy scratch file, verbatim."""

    __tablename__ = "survey_legacy_sections"
    __table_args__ = (UniqueConstraint("origin", "ordinal", name="ux_survey_legacy_sections"),)

    id: str = Field(default_factory=_id, primary_key=True)
    origin: str
    ordinal: int
    heading: str = ""
    body: str
    sha256: str
    imported_at: datetime = Field(default_factory=utcnow)
