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
    _record_repair_brief(session, ticket, stage_key=stage_key, brief=brief)


def _record_repair_brief(session: Session, ticket: Ticket, *, stage_key: str, brief: str) -> None:
    title = _title(stage_key)
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
    if artifact is None:
        return ""
    try:
        rows = json.loads(artifact.content_json or "{}").get("rows", [])
    except json.JSONDecodeError:
        logger.warning("repair brief %s on %s is unreadable", artifact.id, ticket.external_id)
        return ""
    return next((str(row.get("v") or "") for row in rows if row.get("k") == _BRIEF_ROW), "")
