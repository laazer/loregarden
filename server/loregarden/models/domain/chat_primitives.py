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
    | CalendarEventPart,
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
    }
)
