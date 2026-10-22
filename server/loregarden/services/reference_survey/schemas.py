"""What a survey caller hands in, validated at the boundary.

The rules that can be checked on the payload alone live here, so a malformed
record is refused before any row is read: a count with no denominator, a corpus
called complete with no reason why, a finding citing no evidence.
"""

from __future__ import annotations

from loregarden.models.domain.survey_enums import (
    SurveyEpistemic,
    SurveyFindingClaim,
    SurveyPassMode,
    SurveyPassOutcome,
    SurveyReadState,
    SurveySourceKind,
)
from loregarden.models.domain.work_item_types import WorkItemType
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceIn(_In):
    source_id: str
    pass_id: str | None = None
    epistemic: SurveyEpistemic
    operation: str = Field(min_length=1)
    result: str = ""
    authority: str = ""
    limitations: str = ""
    residual_unknowns: str = ""
    corpus: list[str] = Field(default_factory=list)
    corpus_complete: bool = False
    completeness_basis: str = ""
    examined_count: int | None = Field(default=None, ge=0)
    total_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _counts_and_completeness(self) -> EvidenceIn:
        if (self.examined_count is None) != (self.total_count is None):
            raise ValueError(
                "A count without its denominator is not a result: give examined_count and "
                "total_count together, or neither."
            )
        if self.examined_count is not None and self.examined_count > (self.total_count or 0):
            raise ValueError("examined_count cannot exceed total_count")
        if self.corpus_complete:
            if not self.corpus or not self.completeness_basis:
                raise ValueError(
                    "A corpus called complete must name its paths (corpus) and say why it holds "
                    "everything the question could be answered from (completeness_basis)."
                )
            if self.examined_count is not None and self.examined_count != self.total_count:
                raise ValueError(
                    f"The corpus is called complete but {self.examined_count} of "
                    f"{self.total_count} items were examined. Exhausting a page is not "
                    "completeness; record it as incomplete."
                )
        return self


class FindingIn(_In):
    source_id: str
    pass_id: str | None = None
    claim: SurveyFindingClaim
    epistemic: SurveyEpistemic
    statement: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class RetractIn(_In):
    finding_id: str
    reason: str = Field(min_length=1)
    replaced_by_id: str | None = None


class ResolveIn(_In):
    evidence_id: str
    resolution: str = Field(min_length=1)


class UnknownOpenIn(_In):
    source_id: str
    pass_id: str | None = None
    question: str = Field(min_length=1)
    authority_required: str = Field(min_length=1)
    supporting: list[str] = Field(default_factory=list)
    contradicting: list[str] = Field(default_factory=list)


class UnknownUpdateIn(_In):
    unknown_id: str
    revision: int = Field(ge=1)
    supporting: list[str] = Field(default_factory=list)
    contradicting: list[str] = Field(default_factory=list)
    authority_required: str | None = None
    resolve: ResolveIn | None = None


class SourceIn(_In):
    #: Every workspace whose backlog this source should answer; the first is home
    #: (where carrier tickets and inbox cards live). One pass reads for all.
    workspace_slugs: list[str] = Field(min_length=1)
    kind: SurveySourceKind
    url: str = Field(min_length=1)
    name: str = ""
    question: str = Field(min_length=1)
    queued_reason: str = Field(min_length=1)
    priority: int = Field(default=100, ge=0)


#: URLs one bulk add takes: each is its own source and, when new, its own inbox card.
MAX_BULK_URLS = 50


class BulkSourceIn(_In):
    """Several URLs proposed together, for the same workspaces, question and reason."""

    workspace_slugs: list[str] = Field(min_length=1)
    urls: list[str] = Field(min_length=1, max_length=MAX_BULK_URLS)
    #: One kind for every URL, or None to tell each URL's kind from its host.
    kind: SurveySourceKind | None = None
    question: str = Field(min_length=1)
    queued_reason: str = Field(min_length=1)
    priority: int = Field(default=100, ge=0)


class PassRequestIn(_In):
    source_id: str
    mode: SurveyPassMode
    question: str = Field(min_length=1)
    #: Map paths to read; empty means the feeder slices the map itself.
    paths: list[str] = Field(default_factory=list)


class ReadIn(_In):
    path: str = Field(min_length=1)
    state: SurveyReadState
    files_read: int = Field(default=0, ge=0)
    #: For a path not in the map (a single file): its own file count.
    file_count: int | None = Field(default=None, ge=0)
    note: str = ""


class PassFinishIn(_In):
    pass_id: str
    outcome: SurveyPassOutcome
    outcome_note: str = Field(min_length=1)
    stopped_at: str = Field(min_length=1)
    #: Map paths still worth reading, best first; they become the next pass's slice.
    unread_priority: list[str] = Field(default_factory=list)


class NewTicketIn(_In):
    title: str = Field(min_length=1)
    #: Which of the source's workspaces the ticket belongs to; needed when it serves several.
    workspace_slug: str | None = None
    #: The parent, by external id. Hierarchy is checked before anything is created.
    parent: str = Field(min_length=1)
    work_item_type: WorkItemType = WorkItemType.TASK
    description: str = Field(min_length=1)
    priority: int = Field(default=3, ge=1, le=3)


class TicketWriteIn(_In):
    """Land findings on a ticket: append to one that exists, or create one."""

    pass_id: str
    finding_ids: list[str] = Field(min_length=1)
    #: The ticket the findings answer, by external id. Mutually exclusive with `create`.
    ticket: str | None = None
    create: NewTicketIn | None = None
    criteria: list[str] = Field(default_factory=list)
    #: The finding prose; the heading, licence line and evidence lines are added.
    block: str = Field(min_length=1)
    #: Creating while similar open tickets exist needs a reason they do not fit.
    force_new_reason: str = ""
    #: A defect in our own tooling hit during the pass, filed as a bug.
    self_defect: bool = False

    @model_validator(mode="after")
    def _one_target(self) -> TicketWriteIn:
        if (self.ticket is None) == (self.create is None):
            raise ValueError("Name exactly one of ticket (append) or create (new ticket)")
        if self.self_defect and self.create is None:
            raise ValueError("A self-defect is filed as a new bug: use create")
        return self
