"""The initiative planner's two MCP tools: read a plan, propose a schedule.

Own module, like `doctor_tool`, so `mcp/tools.py` stops growing. The planner
never writes targets: `loregarden_propose_initiative_schedule` files a pending
proposal that a person accepts on the Initiatives page.
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
from loregarden.services.initiative_plan_service import (
    plan_payload,
    plan_view,
    propose_schedule,
)

_INITIATIVE_PROP = {
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
        "inputSchema": {
            "type": "object",
            "properties": {"initiative_id": _INITIATIVE_PROP},
            "required": ["initiative_id"],
            "additionalProperties": False,
        },
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
                "initiative_id": _INITIATIVE_PROP,
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
]


_Request = TypeVar("_Request", bound=SQLModel)


class _PlanRequest(SQLModel):
    model_config = ConfigDict(extra="forbid")

    initiative_id: str


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


def _resolve_initiative_id(session: Session, ref: str) -> str:
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


def get_initiative_plan(session: Session, arguments: dict[str, Any]) -> str:
    request = _parse(_PlanRequest, McpTool.GET_INITIATIVE_PLAN, arguments)
    return plan_payload(plan_view(session, _resolve_initiative_id(session, request.initiative_id)))


def propose_initiative_schedule(session: Session, arguments: dict[str, Any]) -> str:
    request = _parse(_ProposeRequest, McpTool.PROPOSE_INITIATIVE_SCHEDULE, arguments)
    initiative_id = _resolve_initiative_id(session, request.initiative_id)
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
    McpTool.GET_INITIATIVE_PLAN.value: get_initiative_plan,
    McpTool.PROPOSE_INITIATIVE_SCHEDULE.value: propose_initiative_schedule,
}
