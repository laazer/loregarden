"""The initiative planner's MCP tools: read a plan, propose a schedule, drive
the autopilot.

Own module, like `doctor_tool`, so `mcp/tools.py` stops growing. The planner
never writes targets: `loregarden_propose_initiative_schedule` files a pending
proposal that a person accepts on the Initiatives page. Every tool here but
that one runs as an operation (`services/operations/initiatives.py`); this
holds their schemas.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import ConfigDict, ValidationError
from sqlmodel import Session, SQLModel, select

from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import (
    ProposalSource,
    ScheduleMode,
    ScheduleProposalCreate,
    ScheduleTargetInput,
    Ticket,
    WorkItemType,
)
from loregarden.services.initiative_plan_service import propose_schedule

INITIATIVE_PROP = {
    "type": "string",
    "description": "Initiative id, or its external id (e.g. init-tinkercg-build-1).",
}


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.GET_INITIATIVE_PLAN,
        "description": (
            "Read an initiative's schedule: per milestone the open/total work items, "
            "target date, forecast date (measured pace in plan order, never before the "
            "agent run-time floor), earliest_date floor, drift and status; per "
            "workspace the pace it was measured from; the plan mode; and any pending "
            "proposal. Read-only."
        ),
    },
    {
        "name": McpTool.PROPOSE_INITIATIVE_SCHEDULE,
        "description": (
            "Propose target dates and order for an initiative's milestones. Files a "
            "pending proposal the operator accepts or discards; it replaces any "
            "proposal still pending, so send every row you want changed. Targets may "
            "name only the initiative and its milestones."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "initiative_id": INITIATIVE_PROP,
                "rationale": {
                    "type": "string",
                    "description": "2-5 sentences the operator reads beside the diff.",
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticket_id": {"type": "string"},
                            "target_date": {
                                "type": ["string", "null"],
                                "description": "YYYY-MM-DD, or null to clear the target.",
                            },
                            "plan_order": {
                                "type": "integer",
                                "minimum": 0,
                                "description": "Sequence within the workspace, from 0.",
                            },
                        },
                        "required": ["ticket_id"],
                        "additionalProperties": False,
                    },
                },
                "mode": {"type": "string", "enum": [m.value for m in ScheduleMode]},
                "source": {"type": "string", "enum": [s.value for s in ProposalSource]},
            },
            "required": ["initiative_id", "rationale", "items"],
            "additionalProperties": False,
        },
    },
    {
        "name": McpTool.SET_INITIATIVE_AUTOPILOT,
        "description": (
            "Turn the autopilot of an initiative, milestone or parent ticket (feature, "
            "capability) on or off, or set how many of its tickets may run at once (1-12). On, "
            "it queues ready tickets in its subtree every minute — critical path first, up to "
            "max_parallel — and never tickets that need a person. A blocked ticket it retries "
            "once per switch-on when a re-run can still clear it: an interrupted run, a pinned "
            "scope reroute, a failed stage with budget left, or a harness/work block whose repair "
            "turn is unspent. It stops itself when three "
            "tickets it started end blocked. Turning it on queues the first batch immediately. "
            "Off means finish what is running, then stop: tickets it queued that have not "
            "started are held in their lanes, and turning it back on puts them back."
        ),
    },
    {
        "name": McpTool.START_INITIATIVE_WORK,
        "description": (
            "Queue specific tickets of the initiative now. Each must be READY in the plan "
            "(prerequisites done and landed, nothing waiting on a person); anything else is "
            "refused with the reason. Returns ticket id -> outcome."
        ),
    },
    {
        "name": McpTool.MARK_NEEDS_PERSON,
        "description": (
            "Mark plan tickets as needing a person (a decision, research, legal, a physical "
            "test) so the autopilot never hands them to an agent; needs_person=false clears it."
        ),
    },
]


_Request = TypeVar("_Request", bound=SQLModel)


class _ProposeRequest(SQLModel):
    model_config = ConfigDict(extra="forbid")

    initiative_id: str
    rationale: str
    items: list[ScheduleTargetInput]
    mode: ScheduleMode | None = None
    source: ProposalSource = ProposalSource.CHAT


def _parse(model: type[_Request], tool: McpTool, arguments: dict[str, Any]) -> _Request:
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(f"invalid arguments for {tool.value}: {exc}") from exc


def resolve_initiative_id(session: Session, ref: str) -> str:
    """Accept the uuid or the external id — agents see both."""
    ref = ref.strip()
    if session.get(Ticket, ref) is not None:
        return ref
    match = session.exec(
        select(Ticket.id).where(
            Ticket.external_id == ref,
            Ticket.work_item_type == WorkItemType.INITIATIVE,
        )
    ).first()
    if match is None:
        raise ValueError(f"Initiative not found: {ref}")
    return match


def propose_initiative_schedule(session: Session, arguments: dict[str, Any]) -> str:
    request = _parse(_ProposeRequest, McpTool.PROPOSE_INITIATIVE_SCHEDULE, arguments)
    initiative_id = resolve_initiative_id(session, request.initiative_id)
    proposal = propose_schedule(
        session,
        initiative_id,
        ScheduleProposalCreate(rationale=request.rationale, items=request.items, mode=request.mode),
        source=request.source,
    )
    return json.dumps(
        {"proposal_id": proposal.id, "status": proposal.status.value, "items": len(request.items)}
    )


HANDLERS = {
    McpTool.PROPOSE_INITIATIVE_SCHEDULE.value: propose_initiative_schedule,
}
