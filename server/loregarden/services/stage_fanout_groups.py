"""Persistence helpers for stage fan-out groups and attempts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar

from loregarden.core.workflow_loader import get_template_stages_at_version
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    StageFanoutAttempt,
    StageFanoutAttemptStatus,
    StageFanoutGroup,
    StageFanoutGroupStatus,
    StageFanoutOutcome,
    Ticket,
    WorkflowInstance,
    WorkflowTemplate,
    Worktree,
    utcnow,
)
from loregarden.services.workflow_state import parse_stage_map, serialize_stage_map
from sqlmodel import Session, select

T = TypeVar("T")

_TERMINAL_ATTEMPT_STATUSES = {
    StageFanoutAttemptStatus.SUCCEEDED,
    StageFanoutAttemptStatus.FAILED,
    StageFanoutAttemptStatus.CANCELLED,
    StageFanoutAttemptStatus.DECLINED,
    StageFanoutAttemptStatus.PROMOTED,
}


def create_group(
    session: Session,
    ticket_id: str,
    stage_key: str,
    attempt_count: int,
    orchestration_run_id: str | None = None,
) -> StageFanoutGroup:
    if attempt_count < 1:
        raise ValueError("attempt_count must be a positive integer")
    stage_key = stage_key.strip()
    if not stage_key:
        raise ValueError("stage_key must be non-empty")

    ticket = _require(session, Ticket, ticket_id, "ticket_id")
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    if instance is None:
        raise ValueError(f"ticket_id {ticket_id!r} has no workflow instance")
    template = _require(session, WorkflowTemplate, instance.template_id, "workflow template")
    stages = get_template_stages_at_version(session, template, instance.template_version)
    stage_map = parse_stage_map(instance, stages)
    if stage_key not in stage_map:
        raise ValueError(f"stage_key {stage_key!r} does not belong to ticket workflow")

    if orchestration_run_id is not None:
        orchestration = _require(
            session, OrchestrationRun, orchestration_run_id, "orchestration_run_id"
        )
        if (
            orchestration.ticket_id != ticket.id
            or orchestration.workspace_id != ticket.workspace_id
        ):
            raise ValueError("orchestration_run_id must belong to the same ticket and workspace")

    group = StageFanoutGroup(
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        orchestration_run_id=orchestration_run_id,
        stage_key=stage_key,
        attempt_count=attempt_count,
        pre_fanout_workflow_stage_key=ticket.workflow_stage_key,
        pre_fanout_workflow_stage_status=_enum_value(ticket.workflow_stage_status),
        pre_fanout_stage_map_json=serialize_stage_map(stage_map, stages),
        pre_fanout_next_agent=ticket.next_agent,
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    _restore_group_datetimes(group)
    return group


def create_attempt(
    session: Session,
    group_id: str,
    *,
    attempt_index: int,
    attempt_name: str | None = None,
) -> StageFanoutAttempt:
    if attempt_index < 0:
        raise ValueError("attempt_index must be zero or greater")
    _require(session, StageFanoutGroup, group_id, "group_id")
    existing = session.exec(
        select(StageFanoutAttempt).where(
            StageFanoutAttempt.group_id == group_id,
            StageFanoutAttempt.attempt_index == attempt_index,
        )
    ).first()
    if existing is not None:
        raise ValueError("attempt_index must be unique within a group")

    attempt = StageFanoutAttempt(
        group_id=group_id,
        attempt_index=attempt_index,
        attempt_name=(attempt_name or "").strip() or f"Attempt {attempt_index + 1}",
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    _restore_attempt_datetimes(attempt)
    return attempt


def link_attempt_run(
    session: Session,
    attempt_id: str,
    agent_run_id: str,
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> StageFanoutAttempt:
    attempt = _require(session, StageFanoutAttempt, attempt_id, "attempt_id")
    group = _require(session, StageFanoutGroup, attempt.group_id, "group_id")
    run = _require(session, AgentRun, agent_run_id, "agent_run_id")
    _validate_run_context(run, group)
    if attempt.worktree_id is not None:
        worktree = _require(session, Worktree, attempt.worktree_id, "worktree_id")
        if worktree.agent_run_id != agent_run_id:
            raise ValueError("agent_run_id must match the linked attempt worktree")
    duplicate = session.exec(
        select(StageFanoutAttempt).where(
            StageFanoutAttempt.group_id == group.id,
            StageFanoutAttempt.agent_run_id == agent_run_id,
            StageFanoutAttempt.id != attempt.id,
        )
    ).first()
    if duplicate is not None:
        raise ValueError("agent_run_id is already linked within this group")

    attempt.agent_run_id = agent_run_id
    attempt.started_at = started_at or run.started_at or attempt.started_at
    attempt.finished_at = finished_at or run.finished_at or attempt.finished_at
    attempt.updated_at = utcnow()
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    _restore_attempt_datetimes(attempt)
    return attempt


def link_attempt_worktree(
    session: Session, attempt_id: str, worktree_id: str
) -> StageFanoutAttempt:
    attempt = _require(session, StageFanoutAttempt, attempt_id, "attempt_id")
    group = _require(session, StageFanoutGroup, attempt.group_id, "group_id")
    worktree = _require(session, Worktree, worktree_id, "worktree_id")
    if worktree.workspace_id != group.workspace_id:
        raise ValueError("worktree_id must belong to the same workspace")
    owning_run = _require(session, AgentRun, worktree.agent_run_id, "worktree agent_run_id")
    _validate_run_context(owning_run, group)
    if attempt.agent_run_id and worktree.agent_run_id != attempt.agent_run_id:
        raise ValueError("worktree agent_run_id must match the linked attempt run")
    duplicate = session.exec(
        select(StageFanoutAttempt).where(
            StageFanoutAttempt.group_id == group.id,
            StageFanoutAttempt.worktree_id == worktree_id,
            StageFanoutAttempt.id != attempt.id,
        )
    ).first()
    if duplicate is not None:
        raise ValueError("worktree_id is already linked within this group")

    attempt.worktree_id = worktree_id
    attempt.branch = worktree.branch
    attempt.updated_at = utcnow()
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    _restore_attempt_datetimes(attempt)
    return attempt


def update_attempt_status(
    session: Session,
    attempt_id: str,
    status: str | StageFanoutAttemptStatus,
    *,
    failure_details: str | None = None,
) -> StageFanoutAttempt:
    attempt = _require(session, StageFanoutAttempt, attempt_id, "attempt_id")
    next_status = _coerce_attempt_status(status)
    now = utcnow()
    attempt.status = next_status
    if next_status == StageFanoutAttemptStatus.RUNNING and attempt.started_at is None:
        attempt.started_at = now
    if next_status in _TERMINAL_ATTEMPT_STATUSES and attempt.finished_at is None:
        attempt.finished_at = now
    if failure_details is not None and next_status in {
        StageFanoutAttemptStatus.FAILED,
        StageFanoutAttemptStatus.CANCELLED,
        StageFanoutAttemptStatus.DECLINED,
    }:
        attempt.failure_details = failure_details
    attempt.updated_at = now
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    _restore_attempt_datetimes(attempt)
    return attempt


def settle_group(
    session: Session,
    group_id: str,
    *,
    outcome: str | StageFanoutOutcome,
    winner_attempt_id: str | None = None,
    declined_reason: str = "",
    failure_summary: str = "",
    status: str | StageFanoutGroupStatus | None = None,
) -> StageFanoutGroup:
    group = _require(session, StageFanoutGroup, group_id, "group_id")
    next_outcome = _coerce_outcome(outcome)
    next_status = _coerce_group_status(status) if status is not None else _status_for(next_outcome)

    if next_outcome == StageFanoutOutcome.PROMOTED:
        if winner_attempt_id is None:
            raise ValueError("winner_attempt_id is required for a promoted outcome")
        winner = _require(session, StageFanoutAttempt, winner_attempt_id, "winner_attempt_id")
        if winner.group_id != group.id:
            raise ValueError("winner_attempt_id must belong to the same group")
        group.winner_attempt_id = winner_attempt_id
    else:
        group.winner_attempt_id = None

    now = utcnow()
    group.status = next_status
    group.outcome = next_outcome
    group.declined_reason = declined_reason
    group.failure_summary = failure_summary
    group.updated_at = now
    if next_outcome != StageFanoutOutcome.PENDING and group.settled_at is None:
        group.settled_at = now
    session.add(group)
    session.commit()
    session.refresh(group)
    _restore_group_datetimes(group)
    return group


def serialize_group(session: Session, group_id: str) -> dict:
    group = _require(session, StageFanoutGroup, group_id, "group_id")
    attempts = session.exec(
        select(StageFanoutAttempt)
        .where(StageFanoutAttempt.group_id == group.id)
        .order_by(StageFanoutAttempt.attempt_index)
    ).all()
    return {
        "id": group.id,
        "workspace_id": group.workspace_id,
        "ticket_id": group.ticket_id,
        "orchestration_run_id": group.orchestration_run_id,
        "stage_key": group.stage_key,
        "attempt_count": group.attempt_count,
        "pre_fanout_workflow_stage_key": group.pre_fanout_workflow_stage_key,
        "pre_fanout_workflow_stage_status": group.pre_fanout_workflow_stage_status,
        "pre_fanout_stage_map_json": group.pre_fanout_stage_map_json,
        "pre_fanout_next_agent": group.pre_fanout_next_agent,
        "status": _enum_value(group.status),
        "outcome": _enum_value(group.outcome),
        "winner_attempt_id": group.winner_attempt_id,
        "declined_reason": group.declined_reason,
        "failure_summary": group.failure_summary,
        "created_at": _dt(group.created_at),
        "updated_at": _dt(group.updated_at),
        "settled_at": _dt(group.settled_at),
        "attempts": [_serialize_attempt(attempt) for attempt in attempts],
    }


def _serialize_attempt(attempt: StageFanoutAttempt) -> dict:
    return {
        "id": attempt.id,
        "attempt_index": attempt.attempt_index,
        "attempt_name": attempt.attempt_name,
        "agent_run_id": attempt.agent_run_id,
        "worktree_id": attempt.worktree_id,
        "branch": attempt.branch,
        "status": _enum_value(attempt.status),
        "started_at": _dt(attempt.started_at),
        "finished_at": _dt(attempt.finished_at),
        "failure_details": attempt.failure_details,
    }


def _require(session: Session, model: type[T], row_id: str, label: str) -> T:
    row = session.get(model, row_id)
    if row is None:
        raise ValueError(f"{label} {row_id!r} was not found")
    return row


def _validate_run_context(run: AgentRun, group: StageFanoutGroup) -> None:
    if run.ticket_id != group.ticket_id or run.workspace_id != group.workspace_id:
        raise ValueError("agent_run_id must belong to the same ticket and workspace")
    if group.orchestration_run_id and run.orchestration_run_id != group.orchestration_run_id:
        raise ValueError("agent_run_id must belong to the same orchestration")


def _coerce_attempt_status(value: str | StageFanoutAttemptStatus) -> StageFanoutAttemptStatus:
    try:
        return (
            value
            if isinstance(value, StageFanoutAttemptStatus)
            else StageFanoutAttemptStatus(value)
        )
    except ValueError as exc:
        raise ValueError(f"status {value!r} is not a valid fan-out attempt status") from exc


def _coerce_outcome(value: str | StageFanoutOutcome) -> StageFanoutOutcome:
    try:
        return value if isinstance(value, StageFanoutOutcome) else StageFanoutOutcome(value)
    except ValueError as exc:
        raise ValueError(f"outcome {value!r} is not a valid fan-out group outcome") from exc


def _coerce_group_status(value: str | StageFanoutGroupStatus) -> StageFanoutGroupStatus:
    try:
        return value if isinstance(value, StageFanoutGroupStatus) else StageFanoutGroupStatus(value)
    except ValueError as exc:
        raise ValueError(f"status {value!r} is not a valid fan-out group status") from exc


def _status_for(outcome: StageFanoutOutcome) -> StageFanoutGroupStatus:
    if outcome in (StageFanoutOutcome.PROMOTED, StageFanoutOutcome.DECLINED):
        return StageFanoutGroupStatus.SETTLED
    if outcome == StageFanoutOutcome.CANCELLED:
        return StageFanoutGroupStatus.CANCELLED
    if outcome == StageFanoutOutcome.FAILED:
        return StageFanoutGroupStatus.FAILED
    return StageFanoutGroupStatus.OPEN


def _dt(value: datetime | None) -> str | None:
    value = _as_utc(value)
    return value.isoformat() if value is not None else None


def _enum_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _restore_group_datetimes(group: StageFanoutGroup) -> None:
    group.created_at = _as_utc(group.created_at)
    group.updated_at = _as_utc(group.updated_at)
    group.settled_at = _as_utc(group.settled_at)


def _restore_attempt_datetimes(attempt: StageFanoutAttempt) -> None:
    attempt.started_at = _as_utc(attempt.started_at)
    attempt.finished_at = _as_utc(attempt.finished_at)
    attempt.created_at = _as_utc(attempt.created_at)
    attempt.updated_at = _as_utc(attempt.updated_at)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
