"""What a Studio publish would change about the workflow that actually runs."""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class StageFieldDrift(SQLModel):
    """One field of one stage that differs between draft and template.

    Values are rendered with `repr` rather than typed: this crosses the API to be
    displayed, and a stage field may be a bool, a string, or a list of routes.
    """

    field: str
    draft: str
    template: str


class StrandedInstances(SQLModel):
    """Live tickets that would lose the stage they are currently sitting on."""

    count: int = 0
    stage_keys: list[str] = Field(default_factory=list)
    ticket_ids: list[str] = Field(default_factory=list)


class WorkflowDrift(SQLModel):
    """The difference between a draft and the template it publishes to.

    `published=False` means the draft has never been published, which is not
    drift — there is nothing to have drifted from.
    """

    slug: str
    published_template_slug: str = ""
    published: bool = False
    drifted: bool = False
    stages_added: list[str] = Field(default_factory=list)
    stages_removed: list[str] = Field(default_factory=list)
    stages_changed: dict[str, list[StageFieldDrift]] = Field(default_factory=dict)
    draft_transition_count: int = 0
    template_transition_count: int = 0
    template_version: int = 0
    stranded: StrandedInstances = Field(default_factory=StrandedInstances)
