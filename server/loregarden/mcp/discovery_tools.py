"""`loregarden_triage_discovery`: promote a run's finding into the work, or close it.

A ticket an agent run files is held out of its parent's work until triaged
(`services.discovered_work`). This is the triage: promoting makes it ordinary
work its parent's drive will run and wait for; closing sets it ``wont_do``
through the same path any manual state change takes.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import enum_string_prop, string_prop, tool_schema
from loregarden.models.domain import DiscoveryTriage, TicketState, UpdateTicketRequest
from loregarden.services.discovered_work import triage
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

DISCOVERY_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.TRIAGE_DISCOVERY,
        "description": (
            "Triage a ticket an agent run filed as a finding. Until triaged it stays where it "
            "was filed, its parent neither runs it nor waits for it, and nothing may start it. "
            "action=promote makes it its parent's work; action=close sets it wont_do. A "
            "finding worth keeping but not now: promote it and leave it in the backlog."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("The filed ticket (UUID or external_id)."),
                "action": enum_string_prop("promote or close.", [a.value for a in DiscoveryTriage]),
            },
            required=["ticket_id", "action"],
        ),
    }
]


def _triage_discovery(session: Session, arguments: dict[str, Any]) -> str:
    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    action = DiscoveryTriage(arguments["action"])
    discovery = triage(session, ticket, action, actor="triage")
    if action is DiscoveryTriage.CLOSE:
        OrchestrationService(session).update_ticket_manual(
            ticket, UpdateTicketRequest(state=TicketState.WONT_DO)
        )
    session.commit()
    return json.dumps(
        {
            "ticket": ticket.external_id,
            "state": ticket.state.value,
            "triaged_by": discovery.triaged_by,
        },
        indent=2,
    )


#: Registered in `tool_registry.EXTENDED_TOOLS`, not `execute_tool`'s chain.
HANDLERS = {McpTool.TRIAGE_DISCOVERY.value: _triage_discovery}
