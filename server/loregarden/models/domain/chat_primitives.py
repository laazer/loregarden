"""Typed chat content parts — the UI-primitives contract.

Agents emit fenced `` ```loregarden `` blocks containing JSON with a
``primitive`` discriminator. The parser turns those into this union; the
frontend renders each kind as a live interactive card.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

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


class TicketListPart(BaseModel):
    primitive: Literal["ticket_list"] = "ticket_list"
    ticket_ids: list[str] = Field(default_factory=list)
    parent_ticket_id: str | None = None
    title: str | None = None


class StatusColumnPart(BaseModel):
    primitive: Literal["status_column"] = "status_column"
    status: str
    ticket_ids: list[str] = Field(default_factory=list)
    title: str | None = None


class KanbanPart(BaseModel):
    primitive: Literal["kanban"] = "kanban"
    ticket_ids: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    title: str | None = None


class FilterableKanbanPart(BaseModel):
    primitive: Literal["filterable_kanban"] = "filterable_kanban"
    ticket_ids: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
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
    primitive: Literal["qa"] = "qa"
    items: list[QAItem] = Field(default_factory=list)
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
    | GiphyPart,
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
    }
)
