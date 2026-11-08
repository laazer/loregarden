"""The drivers' half of `stage_readiness`: block a ticket whose next stage lacks an input.

Apart from `stage_readiness` because blocking goes through
`orchestration_callbacks`, which imports `orchestration`, and `orchestration`
imports `stage_readiness` for the check at `start_run`.
"""

from __future__ import annotations

from loregarden.models.domain import BlockOrigin, OrchestrationRun, Ticket, WorkflowStageDef
from loregarden.models.domain.block_kinds import BlockKind
from loregarden.services.block_settlement import RepairPolicy
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.stage_readiness import (
    block_line,
    missing_inputs,
    record_inputs_missing,
)
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import next_executable_stage, parse_stage_map
from sqlmodel import Session


def _next_stage(session: Session, ticket: Ticket) -> WorkflowStageDef | None:
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    if instance is None or not stages:
        return None
    target_key = next_executable_stage(stages, parse_stage_map(instance, stages))
    return next((stage for stage in stages if stage.key == target_key), None)


def block_if_unready(session: Session, orch_run: OrchestrationRun, ticket: Ticket) -> bool:
    """What both drivers do before choosing a stage. True when the ticket blocked."""
    target = _next_stage(session, ticket)
    if target is None or not target.required_inputs:
        return False
    missing = missing_inputs(session, ticket, target)
    if not missing:
        return False
    record_inputs_missing(session, ticket, target.key, missing)
    OrchestrationCallbackService(session).block_ticket(
        orch_run,
        ticket,
        origin=BlockOrigin.CONTROL_PLANE,
        repair=RepairPolicy.withheld("An agent turn on this stage cannot supply its own input."),
        stage_key=target.key,
        message=block_line(target.key, missing),
        declared=BlockKind.DECISION,
    )
    return True
