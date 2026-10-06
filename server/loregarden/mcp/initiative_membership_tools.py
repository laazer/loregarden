"""Add a ticket to an initiative, or take it out, without re-parenting it.

Own module beside `initiative_plan_tools`, which is the planner's floor; these
change what an initiative covers, so they stay gated like any ticket write.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ConfigDict, ValidationError
from sqlmodel import Session, SQLModel

from loregarden.mcp.initiative_plan_tools import INITIATIVE_PROP, resolve_initiative_id
from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import Ticket
from loregarden.services.initiative_membership import add_member, remove_member
from loregarden.services.initiative_service import get_initiative
from loregarden.services.ticket_ids import resolve

#: Who a membership change made through MCP is attributed to.
_ACTOR = "agent"

_TICKET_PROP = {
    "type": "string",
    "description": "Ticket id, or its external id (e.g. lg-milestone-that-780).",
}

_SCHEMA = {
    "type": "object",
    "properties": {"initiative_id": INITIATIVE_PROP, "ticket_id": _TICKET_PROP},
    "required": ["initiative_id", "ticket_id"],
    "additionalProperties": False,
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.ADD_INITIATIVE_MEMBER,
        "description": (
            "Track a ticket (any type but an initiative) and its whole subtree in an "
            "initiative's view, plan, forecast and autopilot, WITHOUT re-parenting it: it "
            "keeps its parent, milestone, workspace and integration branch. Refused when the "
            "initiative already covers it (as a member, a child, or under one), when it "
            "contains one of the initiative's members, or when it is an initiative. A ticket "
            "may belong to several initiatives. Returns the initiative's top-level tickets."
        ),
        "inputSchema": _SCHEMA,
    },
    {
        "name": McpTool.REMOVE_INITIATIVE_MEMBER,
        "description": (
            "Stop an initiative tracking a member ticket. The ticket itself is not changed; "
            "a child the initiative parents is detached through update_ticket instead."
        ),
        "inputSchema": _SCHEMA,
    },
]


class _MemberRequest(SQLModel):
    model_config = ConfigDict(extra="forbid")

    initiative_id: str
    ticket_id: str


def _parse(tool: McpTool, arguments: dict[str, Any]) -> _MemberRequest:
    try:
        return _MemberRequest.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(f"invalid arguments for {tool.value}: {exc}") from exc


def _ticket(session: Session, ref: str) -> Ticket:
    ticket = session.get(Ticket, ref.strip()) or resolve(session, ref)
    if ticket is None:
        raise ValueError(f"Ticket not found: {ref}")
    return ticket


def _members_payload(session: Session, initiative_id: str) -> str:
    view = get_initiative(session, initiative_id)
    return json.dumps(
        {
            "initiative": view.external_id,
            "items": [
                {
                    "id": m.id,
                    "external_id": m.external_id,
                    "work_item_type": m.work_item_type.value,
                    "workspace_slug": m.workspace_slug,
                    "member": m.member,
                    "home_milestone": m.home_milestone,
                }
                for m in view.milestones
            ],
        },
        indent=2,
    )


def add_initiative_member(session: Session, arguments: dict[str, Any]) -> str:
    request = _parse(McpTool.ADD_INITIATIVE_MEMBER, arguments)
    initiative_id = resolve_initiative_id(session, request.initiative_id)
    add_member(session, initiative_id, _ticket(session, request.ticket_id).id, actor=_ACTOR)
    return _members_payload(session, initiative_id)


def remove_initiative_member(session: Session, arguments: dict[str, Any]) -> str:
    request = _parse(McpTool.REMOVE_INITIATIVE_MEMBER, arguments)
    initiative_id = resolve_initiative_id(session, request.initiative_id)
    try:
        remove_member(session, initiative_id, _ticket(session, request.ticket_id).id)
    except LookupError as exc:
        raise ValueError(str(exc)) from exc
    return _members_payload(session, initiative_id)


HANDLERS = {
    McpTool.ADD_INITIATIVE_MEMBER.value: add_initiative_member,
    McpTool.REMOVE_INITIATIVE_MEMBER.value: remove_initiative_member,
}
