"""Read and write models for suggesting initiatives from open work.

A suggestion is never stored: it is computed on read (or by one agent turn),
shown for review, and only the ones the operator keeps become initiatives.
"""

from __future__ import annotations

from datetime import date, datetime

from loregarden.models.domain.enums import TicketState
from loregarden.models.domain.plan_enums import ForecastBasis, SuggestionKind, SuggestionSource
from loregarden.models.domain.work_item_types import WorkItemType
from pydantic import BaseModel, Field

#: The sprint lengths the page offers; anything in range is accepted.
MIN_SPRINT_DAYS = 1
MAX_SPRINT_DAYS = 56
DEFAULT_SPRINT_DAYS = 14


class SuggestedItem(BaseModel):
    id: str
    external_id: str
    title: str
    state: TicketState
    work_item_type: WorkItemType
    workspace_slug: str
    #: The milestone a feature or bug would leave; blank for a milestone.
    from_milestone: str
    #: Open work items it carries, in the unit pace is measured in.
    cost: int


class InitiativeSuggestion(BaseModel):
    #: Stable within one response, for the page to key edits by.
    key: str
    kind: SuggestionKind
    title: str
    description: str
    #: Why these items belong together, in a sentence.
    rationale: str
    items: list[SuggestedItem]
    #: Milestones this would take every open feature and bug out of. They have
    #: nothing left to wait on, so the rollup marks them done.
    empties: list[str]
    #: When it should be finished: a sprint's last day; null for a theme.
    target_date: date | None


class SprintSizing(BaseModel):
    days: int
    #: Work items the measured pace finishes in `days`; null when nothing measured.
    capacity: int | None
    #: Open work items already directly under an initiative, charged before this sprint.
    committed: int
    #: Work items the sprint suggestion holds.
    planned: int
    basis: ForecastBasis


class InitiativeSuggestionSet(BaseModel):
    source: SuggestionSource
    suggestions: list[InitiativeSuggestion]
    #: Open milestones without an initiative that no suggestion claimed.
    ungrouped: list[SuggestedItem]
    sprint: SprintSizing
    #: Anything the agent proposed that could not be used, said rather than dropped.
    warnings: list[str]
    generated_at: datetime


class SuggestionRequest(BaseModel):
    sprint_days: int = Field(default=DEFAULT_SPRINT_DAYS, ge=MIN_SPRINT_DAYS, le=MAX_SPRINT_DAYS)


class InitiativeDraft(BaseModel):
    """One suggestion as the operator kept it."""

    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    item_ids: list[str] = Field(min_length=1)
    #: Set as the initiative's plan target, so the plan tracks the deadline.
    target_date: date | None = None


class SuggestionApply(BaseModel):
    initiatives: list[InitiativeDraft] = Field(min_length=1)


class CreatedInitiative(BaseModel):
    id: str
    external_id: str
    title: str
    attached: int


class SuggestionApplyResult(BaseModel):
    created: list[CreatedInitiative]


__all__ = [
    "MIN_SPRINT_DAYS",
    "MAX_SPRINT_DAYS",
    "DEFAULT_SPRINT_DAYS",
    "SuggestedItem",
    "InitiativeSuggestion",
    "SprintSizing",
    "InitiativeSuggestionSet",
    "SuggestionRequest",
    "InitiativeDraft",
    "SuggestionApply",
    "CreatedInitiative",
    "SuggestionApplyResult",
]
