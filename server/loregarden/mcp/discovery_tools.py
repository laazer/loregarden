"""`loregarden_decide_finding`: the scope decision on a finding a run filed.

A ticket an agent run files is undecided until its source ticket's spec owner
decides whether it is that ticket's work (`services.scope_round`). The spec
stage decides during a scope round. An operator may decide any finding.

Who may call it is checked against the run, not the request: an orchestrated
call must come from a spec run on the finding's own source ticket. A reviewer
that could decide its own findings would put the decision back where it was.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.agents.registry import SPEC_AGENT_ID
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import enum_string_prop, string_prop, tool_schema
from loregarden.models.domain import AgentRun, FindingDecision, Ticket
from loregarden.services.discovered_work import undecided
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.scope_round import decide_finding

DISCOVERY_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.DECIDE_FINDING,
        "description": (
            "Decide the scope of a finding an agent run filed against a ticket. Called by that "
            "ticket's spec stage during a scope round, or by an operator. amend: it is the "
            "ticket's own work; give the criterion it will be held to, and every stage after "
            "spec reruns. follow_up: real work beyond the criteria that the ticket's parent "
            "owns and waits for. defer: real, but not this tree's work; it stays in the "
            "backlog and nothing waits for it. drop: not worth doing; closed wont_do. A reason "
            "is always required. Findings about the tests, guards or fixtures the ticket "
            "itself built are its own work: amend."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("The finding (UUID or external_id)."),
                "decision": enum_string_prop(
                    "The scope decision.", [d.value for d in FindingDecision]
                ),
                "reason": string_prop("Why, in a sentence a person can check."),
                "criterion": string_prop(
                    "amend only: the acceptance criterion the source ticket is held to."
                ),
            },
            required=["ticket_id", "decision", "reason"],
        ),
    }
]


def _decider(session: Session, finding: Ticket, *, orchestrated: bool, run_id: str) -> str:
    """Who is deciding, refused unless it is the finding's spec owner or an operator."""
    if not orchestrated:
        return "operator"
    run = session.get(AgentRun, run_id) if run_id else None
    discovery = undecided(session, finding.id)
    if run is None or discovery is None:
        raise ValueError(
            "loregarden_decide_finding from a pipeline agent must come from a supervised run, "
            "on a finding still waiting for its scope decision."
        )
    if run.agent_id != SPEC_AGENT_ID or run.ticket_id != discovery.source_ticket_id:
        raise ValueError(
            "Only the spec stage of the ticket a finding was filed against decides its scope. "
            "Name the finding in your stage report instead."
        )
    return f"run:{run.id}"


def decide_finding_tool(
    session: Session, arguments: dict[str, Any], *, orchestrated: bool, run_id: str
) -> str:
    finding = OrchestrationCallbackService(session).resolve_ticket(ticket_id=arguments["ticket_id"])
    decided_by = _decider(session, finding, orchestrated=orchestrated, run_id=run_id)
    discovery = decide_finding(
        session,
        finding,
        FindingDecision(arguments["decision"]),
        reason=arguments.get("reason") or "",
        decided_by=decided_by,
        criterion=arguments.get("criterion") or "",
    )
    session.refresh(finding)
    return json.dumps(
        {
            "finding": finding.external_id,
            "decision": discovery.decision.value if discovery.decision else None,
            "state": finding.state.value,
            "decided_by": discovery.decided_by,
        },
        indent=2,
    )
