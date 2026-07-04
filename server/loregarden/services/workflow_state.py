"""Keep ticket state, workflow pointer, and per-stage statuses consistent."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowStageView,
)


def initial_stages_json(stages: list[WorkflowStageDef]) -> str:
    ordered = sorted(stages, key=lambda s: s.order)
    return json.dumps([{"key": s.key, "status": StageStatus.PENDING.value} for s in ordered])


def stages_up_to_done_json(stages: list[WorkflowStageDef], completed_key: str) -> str:
    """Mark every stage up to and including completed_key as done; rest pending."""
    ordered = sorted(stages, key=lambda s: s.order)
    keys = [s.key for s in ordered]
    try:
        done_idx = keys.index(completed_key)
    except ValueError:
        done_idx = -1
    payload = []
    for i, stage in enumerate(ordered):
        status = StageStatus.DONE if i <= done_idx else StageStatus.PENDING
        payload.append({"key": stage.key, "status": status.value})
    return json.dumps(payload)


def parse_stage_map(
    instance: WorkflowInstance, stages: list[WorkflowStageDef]
) -> dict[str, StageStatus]:
    ordered = sorted(stages, key=lambda s: s.order)
    raw = json.loads(instance.stages_json or "[]")
    by_key = {item["key"]: StageStatus(item["status"]) for item in raw}
    return {s.key: by_key.get(s.key, StageStatus.PENDING) for s in ordered}


def serialize_stage_map(stage_map: dict[str, StageStatus], stages: list[WorkflowStageDef]) -> str:
    ordered = sorted(stages, key=lambda s: s.order)
    return json.dumps(
        [{"key": s.key, "status": stage_map[s.key].value} for s in ordered if s.key in stage_map]
    )


def _stage_resolved(status: StageStatus) -> bool:
    return status in (StageStatus.DONE, StageStatus.WONT_DO)


def _cursor_stage(
    ticket: Ticket,
    stage_map: dict[str, StageStatus],
    stages: list[WorkflowStageDef],
) -> tuple[str, StageStatus]:
    ordered = sorted(stages, key=lambda s: s.order)
    keys = [s.key for s in ordered]

    for status in (StageStatus.RUNNING, StageStatus.AWAITING, StageStatus.BLOCKED):
        for key in keys:
            if stage_map.get(key) == status:
                return key, status

    key = ticket.workflow_stage_key
    if key and key in stage_map:
        return key, stage_map[key]

    for key in keys:
        if stage_map.get(key) == StageStatus.PENDING:
            return key, StageStatus.PENDING

    last = keys[-1] if keys else ""
    return last, stage_map.get(last, StageStatus.DONE) if last else StageStatus.PENDING


def _derive_ticket_state(
    stage_map: dict[str, StageStatus],
    stages: list[WorkflowStageDef],
    *,
    blocking_issues: str,
    workflow_stage_key: str,
) -> TicketState:
    statuses = list(stage_map.values())
    if any(s == StageStatus.BLOCKED for s in statuses) or blocking_issues:
        return TicketState.BLOCKED

    ordered = sorted(stages, key=lambda s: s.order)
    required = [s for s in ordered if not s.optional]
    if required and all(_stage_resolved(stage_map.get(s.key, StageStatus.PENDING)) for s in required):
        return TicketState.DONE

    if workflow_stage_key or any(s != StageStatus.PENDING for s in statuses):
        return TicketState.IN_PROGRESS

    return TicketState.BACKLOG


def reconcile_workflow_state(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    *,
    persist: bool = True,
) -> dict[str, StageStatus]:
    """Align stages_json, ticket workflow fields, and ticket.state."""
    stage_map = parse_stage_map(instance, stages)
    current_key, current_status = _cursor_stage(ticket, stage_map, stages)
    ticket_state = _derive_ticket_state(
        stage_map,
        stages,
        blocking_issues=ticket.blocking_issues,
        workflow_stage_key=current_key,
    )

    ticket.workflow_stage_key = current_key
    ticket.workflow_stage_status = current_status
    if not ticket.state_locked and ticket.state != TicketState.WONT_DO:
        ticket.state = ticket_state
    ticket.updated_at = datetime.now(timezone.utc)

    instance.current_stage_key = current_key
    instance.stages_json = serialize_stage_map(stage_map, stages)
    instance.updated_at = datetime.now(timezone.utc)

    if current_key:
        stage_def = next((s for s in stages if s.key == current_key), None)
        if stage_def and stage_def.agent_id:
            ticket.next_agent = stage_def.agent_id

    if persist:
        # Caller commits; we only mutate in-memory objects here.
        pass

    return stage_map


def set_stage_status(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    stage_key: str,
    status: StageStatus,
) -> dict[str, StageStatus]:
    stage_map = parse_stage_map(instance, stages)
    if stage_key not in stage_map:
        raise ValueError(f"Unknown stage key: {stage_key}")
    stage_map[stage_key] = status
    instance.stages_json = serialize_stage_map(stage_map, stages)
    reconcile_workflow_state(ticket, instance, stages, persist=False)
    return stage_map


def build_stage_views(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
) -> list[WorkflowStageView]:
    reconcile_workflow_state(ticket, instance, stages, persist=False)
    stage_map = parse_stage_map(instance, stages)
    raw = json.loads(instance.stages_json or "[]")
    note_by_key = {item["key"]: item.get("note", "") for item in raw}
    views: list[WorkflowStageView] = []
    for stage in sorted(stages, key=lambda s: s.order):
        views.append(
            WorkflowStageView(
                key=stage.key,
                name=stage.name,
                status=stage_map[stage.key],
                agent_id=stage.agent_id,
                skill_name=stage.skill_name,
                optional=stage.optional,
                note=note_by_key.get(stage.key, ""),
            )
        )
    return views
