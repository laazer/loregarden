"""What a repair turn is told, kept where dispatch cannot erase it (lg-milestone-that-929).

Both turns that pin the repair agent on a stage (block repair, and the landing
conflict resolver) used to hand their brief over in `ticket.blocking_issues`.
`OrchestrationService._prepare_stage_start` blanks that field the moment the
stage is dispatched, before the executor renders the prompt, so every repair
turn ran without being told why it was there. 782's resolver read "no
blocking_issues", never learned the landing had conflicted, and spent its one
turn calling a tool it is refused.

The brief is therefore also written as an artifact when the turn is armed,
and the prompt reads it from there. `blocking_issues` still carries it for the
workflow pane; it is no longer the only copy.

`arm_repair_turn` is the only way to pin the repair agent, so a turn cannot be
armed without its brief.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    OrchestrationRun,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.workflow_routing import apply_stage_route
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

_BRIEF_ROW = "Brief"


def _title(stage_key: str) -> str:
    return f"Repair brief — {stage_key}"


def arm_repair_turn(
    session: Session,
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    transitions: list[dict[str, str]],
    *,
    stage_key: str,
    brief: str,
    orch_run: OrchestrationRun | None = None,
) -> None:
    """Re-arm `stage_key` with the repair agent pinned, and keep its brief."""
    apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key=stage_key,
        outcome="reject",
        next_stage_key=stage_key,
        next_agent=REPAIR_AGENT_ID,
        blocking_issues=brief,
        orch_run=orch_run,
    )
    _record_brief(session, ticket, title=_title(stage_key), stage_key=stage_key, brief=brief)


def record_gate_fix_brief(session: Session, ticket: Ticket, *, stage_key: str, brief: str) -> None:
    """Keep the brief of a gate-fix retry of `stage_key`, for the dispatch that runs it.

    A retry is the stage's own agent, or the debugger for a failing test; the
    repair turn that follows a spent budget gets its brief from `arm_repair_turn`.
    Does not commit: the caller commits it with the reroute it explains.
    """
    _record_brief(
        session, ticket, title=_gate_fix_title(stage_key), stage_key=stage_key, brief=brief
    )


def _gate_fix_title(stage_key: str) -> str:
    return f"Gate fix brief — {stage_key}"


def _record_brief(
    session: Session, ticket: Ticket, *, title: str, stage_key: str, brief: str
) -> None:
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.CONTEXT,
            title=title,
            content_json=json.dumps(
                {
                    "title": title,
                    "rows": [{"k": "Stage", "v": stage_key}, {"k": _BRIEF_ROW, "v": brief}],
                }
            ),
        )
    )


def repair_brief_for(session: Session, ticket: Ticket, *, stage_key: str, before: datetime) -> str:
    """The newest brief armed on `stage_key` before `before` (the run's creation)."""
    artifact = session.exec(
        select(Artifact)
        .where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == ArtifactKind.CONTEXT,
            Artifact.title == _title(stage_key),
            Artifact.created_at <= before,
        )
        .order_by(Artifact.created_at.desc())
    ).first()
    return _brief_of(artifact, ticket) if artifact is not None else ""


def gate_fix_brief_for(session: Session, ticket: Ticket, *, run: AgentRun) -> str:
    """The gate-fix brief `run` was dispatched to act on, or "".

    Only the first run of the stage after the brief was armed reads it: a later
    run (a rework round, a requeue) is there for its own reason, and an old gate
    failure would read as that reason.
    """
    if run.created_at is None:
        return ""
    artifact = session.exec(
        select(Artifact)
        .where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == ArtifactKind.CONTEXT,
            Artifact.title == _gate_fix_title(run.stage_key),
            Artifact.created_at <= run.created_at,
        )
        .order_by(Artifact.created_at.desc())
    ).first()
    if artifact is None or artifact.created_at is None:
        return ""
    consumed = session.exec(
        select(AgentRun.id).where(
            AgentRun.ticket_id == ticket.id,
            AgentRun.stage_key == run.stage_key,
            AgentRun.id != run.id,
            AgentRun.created_at > artifact.created_at,
            AgentRun.created_at < run.created_at,
        )
    ).first()
    return "" if consumed is not None else _brief_of(artifact, ticket)


def _brief_of(artifact: Artifact, ticket: Ticket) -> str:
    try:
        rows = json.loads(artifact.content_json or "{}").get("rows", [])
    except json.JSONDecodeError:
        logger.warning("brief %s on %s is unreadable", artifact.id, ticket.external_id)
        return ""
    return next((str(row.get("v") or "") for row in rows if row.get("k") == _BRIEF_ROW), "")
