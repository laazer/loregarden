"""The `loregarden_defer_ticket` MCP tool: defer a blocked ticket so its dependents proceed.

Gated like any write on a stage run: deferring lets the tickets waiting on this
one start without its work (`services/ticket_deferral`).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import string_prop, tool_schema
from loregarden.services.deferral_state import is_deferred
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_deferral import defer_ticket, undefer_ticket

TOOL_DEFINITION: dict[str, Any] = {
    "name": McpTool.DEFER_TICKET,
    "description": (
        "Defer a blocked ticket so the tickets waiting on it proceed without it. It stays "
        "blocked; each dependent's brief names it and the reason, its parent cannot reach "
        "done while it is deferred, and the autopilot retries it in sweeps once other work "
        "settles. Only a blocked ticket can be deferred, and only with a reason. "
        "defer=false takes a deferral back."
    ),
    "inputSchema": tool_schema(
        properties={
            "ticket_id": string_prop("The blocked ticket (UUID or external_id)."),
            "reason": string_prop(
                "Why its dependents should go ahead without it; every dependent is told."
            ),
            "defer": {
                "type": "boolean",
                "description": "false takes a deferral back. Default true.",
            },
        },
        required=["ticket_id"],
    ),
}


class _DeferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    reason: str = ""
    defer: bool = True


def defer_ticket_tool(session: Session, arguments: dict[str, Any]) -> str:
    try:
        request = _DeferRequest.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(f"{McpTool.DEFER_TICKET.value}: {exc}") from exc
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=request.ticket_id)
    if request.defer:
        defer_ticket(session, ticket, request.reason, actor="agent")
    else:
        undefer_ticket(session, ticket, actor="agent")
    session.refresh(ticket)
    return json.dumps(
        {
            "ticket_id": ticket.id,
            "external_id": ticket.external_id,
            "deferred": is_deferred(ticket),
            "reason": ticket.deferred_reason,
        }
    )
