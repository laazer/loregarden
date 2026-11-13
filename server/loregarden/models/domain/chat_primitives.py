"""Typed chat content parts — the UI-primitives contract.

Agents emit fenced `` ```loregarden `` blocks containing JSON with a
``primitive`` discriminator. The parser turns those into this union; the
frontend renders each kind as a live interactive card.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CHAT_PRIMITIVES_VERSION = 1


class TextPart(BaseModel):
    primitive: Literal["text"] = "text"
    content: str


class ThinkingPart(BaseModel):
    primitive: Literal["thinking"] = "thinking"
    content: str
    collapsed: bool = True


class TicketPart(BaseModel):
    primitive: Literal["ticket"] = "ticket"
    ticket_id: str
    title: str | None = None


class TicketWorkflowPart(BaseModel):
    primitive: Literal["ticket_workflow"] = "ticket_workflow"
    ticket_id: str
    title: str | None = None


class ParentTicketPart(BaseModel):
    primitive: Literal["parent_ticket"] = "parent_ticket"
    ticket_id: str
    title: str | None = None


class _TicketSetPart(BaseModel):
    """A card whose empty ``ticket_ids`` means "every ticket", not "none".

    Unknown keys are rejected so a misspelled id list fails validation and the
    parser keeps the raw fence visible. Ignoring it instead turned
    ``"items":[{"ticket_id":…}]`` into an unfiltered tree of every workspace,
    shown under the agent's title as if it were the answer.
    """

    model_config = ConfigDict(extra="forbid")


class TicketListPart(_TicketSetPart):
    primitive: Literal["ticket_list"] = "ticket_list"
    ticket_ids: list[str] = Field(default_factory=list)
    parent_ticket_id: str | None = None
    #: Narrow the whole-tree fallback to one workspace; ``None`` means every
    #: workspace, which is what these cards have always shown. Ignored when a
    #: parent or an explicit id list already names the rows.
    workspace_slug: str | None = None
    title: str | None = None


class StatusColumnPart(_TicketSetPart):
    primitive: Literal["status_column"] = "status_column"
    status: str
    ticket_ids: list[str] = Field(default_factory=list)
    #: See :attr:`TicketListPart.workspace_slug`.
    workspace_slug: str | None = None
    title: str | None = None


class KanbanPart(_TicketSetPart):
    primitive: Literal["kanban"] = "kanban"
    ticket_ids: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    #: See :attr:`TicketListPart.workspace_slug`.
    workspace_slug: str | None = None
    title: str | None = None


class FilterableKanbanPart(_TicketSetPart):
    primitive: Literal["filterable_kanban"] = "filterable_kanban"
    ticket_ids: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    #: See :attr:`TicketListPart.workspace_slug`.
    workspace_slug: str | None = None
    title: str | None = None


class AgentPart(BaseModel):
    primitive: Literal["agent"] = "agent"
    agent_id: str | None = None
    slug: str | None = None
    draft: dict[str, Any] | None = None
    title: str | None = None


class WorkflowPart(BaseModel):
    primitive: Literal["workflow"] = "workflow"
    workflow_slug: str | None = None
    draft: dict[str, Any] | None = None
    title: str | None = None


class GatePart(BaseModel):
    primitive: Literal["gate"] = "gate"
    ticket_id: str | None = None
    stage_key: str | None = None
    draft: dict[str, Any] | None = None
    title: str | None = None


class TerminalLine(BaseModel):
    kind: Literal["command", "stdout", "stderr", "status"] = "stdout"
    text: str


class TerminalPart(BaseModel):
    primitive: Literal["terminal"] = "terminal"
    title: str = "Terminal"
    lines: list[TerminalLine] = Field(default_factory=list)
    cwd: str | None = None


class EditPart(BaseModel):
    primitive: Literal["edit"] = "edit"
    target: Literal["agent", "workflow", "gate", "terminal", "text"] = "text"
    target_id: str | None = None
    language: str = "markdown"
    content: str = ""
    # Baseline for an active proposed-edit diff. When set (including ""), the card
    # renders a reviewable diff of original → content instead of a plain editor.
    original: str | None = None
    # Display path used as the comment anchor and editor jump target
    # (e.g. agent_context/agents/planner.md).
    path: str | None = None
    # Workspace whose repo holds `path`; the UI falls back to the current editor workspace.
    workspace_slug: str | None = None
    title: str | None = None


class CalendarEventItem(BaseModel):
    id: str | None = None
    title: str
    starts_at: str
    ends_at: str | None = None
    kind: Literal["cron", "scheduled", "one_time", "plan", "run"] = "one_time"
    ticket_id: str | None = None
    description: str | None = None


class CalendarPart(BaseModel):
    primitive: Literal["calendar"] = "calendar"
    view: Literal["month", "week", "day"] = "month"
    focus_date: str | None = None
    events: list[CalendarEventItem] = Field(default_factory=list)
    title: str | None = None


class CalendarEventPart(BaseModel):
    primitive: Literal["calendar_event"] = "calendar_event"
    event: CalendarEventItem


class WorkspacePart(BaseModel):
    primitive: Literal["workspace"] = "workspace"
    workspace_slug: str
    title: str | None = None


class TodoItem(BaseModel):
    id: str
    text: str
    checked: bool = False


class TodoListPart(BaseModel):
    primitive: Literal["todo_list"] = "todo_list"
    owner: Literal["agent", "user"] = "agent"
    items: list[TodoItem] = Field(default_factory=list)
    title: str | None = None
    # Identity of an agent execution plan across turns. Re-emitting a plan with
    # the same id supersedes the earlier card instead of stacking a new one.
    plan_id: str | None = None


class BranchHistoryPart(BaseModel):
    primitive: Literal["branch_history"] = "branch_history"
    workspace_slug: str
    branch: str
    limit: int = Field(default=8, ge=1, le=50)
    title: str | None = None


class CommitPart(BaseModel):
    primitive: Literal["commit"] = "commit"
    workspace_slug: str
    sha: str
    branch: str | None = None
    title: str | None = None


class QAItem(BaseModel):
    id: str
    question: str
    answer: str = ""


class QAPart(BaseModel):
    """A question the agent needs answered before it can continue.

    `items` must be non-empty, because an empty one is not a question — it is a
    malformed fence, and it used to halt the run anyway: `_agent_plan_reply_complete`
    treats any qa part as "needs the operator", so a zero-item card parked plan
    execution behind a card that asked nothing and offered a Send button that
    could never enable. Failing validation instead degrades the fence to visible
    text (see `chat_primitives.parser`), which the operator can read and the
    plan loop treats as an incomplete attempt to continue from.
    """

    primitive: Literal["qa"] = "qa"
    items: list[QAItem] = Field(min_length=1)
    title: str | None = None
    prompt: str | None = None
    interactive: bool = True


class BtwPart(BaseModel):
    """An aside and its answer, rendered as one card in the ticket transcript.

    Every answer carried here is an observer's, read off the run's log — the
    working agent's own reply to an escalated aside goes to that run's log, not
    to a card. `observed_*` therefore describes what the answer is *about* and
    never who gave it, which is the one thing a reader must not get wrong.

    Unlike every other primitive here, this one is built by the server rather
    than emitted by a model — there is no fence for it.
    """

    primitive: Literal["btw"] = "btw"
    exchange_id: str
    #: Carried so the card can reach the aside's live state — whether the run it
    #: asked about is still steerable is a fact about *now*, not about write time.
    ticket_id: str
    question: str
    answer: str = ""
    #: The run the question was about, when one was going.
    observed_run_id: str | None = None
    observed_agent_id: str | None = None
    observed_stage_key: str | None = None
    escalated: bool = False
    #: False renders the card from this part alone — no live lookup, no
    #: escalation. For previews, whose ``exchange_id`` refers to no real aside.
    interactive: bool = True
    title: str | None = None


class GiphyPart(BaseModel):
    primitive: Literal["giphy"] = "giphy"
    giphy_id: str | None = None
    url: str | None = None
    alt: str = "Animated GIF"
    title: str | None = None
    caption: str | None = None


class ResearchCandidatesPart(BaseModel):
    """A research question's comparison table, read from the record by id.

    Carries only the id on purpose: the card fetches the candidates and claims
    as recorded, so it cannot show a claim the service refused.
    """

    primitive: Literal["research_candidates"] = "research_candidates"
    question_id: str
    title: str | None = None


class ResearchClaimPart(BaseModel):
    """One project and one capability: the claim that stands and the lines behind it.

    Names the project and capability, not a claim id, so the card always shows
    the claim that stands now, a person's refutation included.
    """

    primitive: Literal["research_claim"] = "research_claim"
    url: str
    capability: str
    title: str | None = None


class ResearchProjectPart(BaseModel):
    """Everything research knows about one project, across every question, read by URL."""

    primitive: Literal["research_project"] = "research_project"
    url: str
    title: str | None = None


class ResearchDiffPart(BaseModel):
    """What one run of a research question changed, read from the record by question id."""

    primitive: Literal["research_diff"] = "research_diff"
    question_id: str
    #: The run it describes; None for the question's latest run.
    run: int | None = None
    title: str | None = None


class PrCyclePart(BaseModel):
    """A branch's way to the base: pre-push checks, then PR checks, then the merge queue.

    ``pre_push`` is the one stage nothing can read back later, so the writer
    records it; the card reads the PR and its queue entry live by ``branch``
    (or ``pr_number`` when the PR's head is not that branch).
    """

    model_config = ConfigDict(extra="forbid")

    primitive: Literal["pr_cycle"] = "pr_cycle"
    workspace_slug: str
    branch: str
    pr_number: int | None = Field(default=None, gt=0)
    #: How the push's pre-push suite went; "unknown" when nothing recorded it.
    pre_push: Literal["passed", "failed", "unknown"] = "unknown"
    #: The failing push's output, which is what a repair turn needs to read.
    pre_push_detail: str = ""
    title: str | None = None


ChatPart = Annotated[
    TextPart
    | ThinkingPart
    | TicketPart
    | TicketWorkflowPart
    | ParentTicketPart
    | TicketListPart
    | StatusColumnPart
    | KanbanPart
    | FilterableKanbanPart
    | AgentPart
    | WorkflowPart
    | GatePart
    | TerminalPart
    | EditPart
    | CalendarPart
    | CalendarEventPart
    | WorkspacePart
    | TodoListPart
    | BranchHistoryPart
    | CommitPart
    | QAPart
    | BtwPart
    | GiphyPart
    | ResearchCandidatesPart
    | ResearchClaimPart
    | ResearchProjectPart
    | ResearchDiffPart
    | PrCyclePart,
    Field(discriminator="primitive"),
]

KNOWN_PRIMITIVES = frozenset(
    {
        "text",
        "thinking",
        "ticket",
        "ticket_workflow",
        "parent_ticket",
        "ticket_list",
        "status_column",
        "kanban",
        "filterable_kanban",
        "agent",
        "workflow",
        "gate",
        "terminal",
        "edit",
        "calendar",
        "calendar_event",
        "workspace",
        "todo_list",
        "branch_history",
        "commit",
        "qa",
        "btw",
        "giphy",
        "research_candidates",
        "research_claim",
        "research_project",
        "research_diff",
        "pr_cycle",
    }
)
