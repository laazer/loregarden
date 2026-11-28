"""Clear a block and hand the stage back its dispatch budget.

The circuit breaker persists its dispatch markers across orchestration runs
precisely so a stage cannot refresh its own budget by restarting. Only a
deliberate decision clears it — an operator's, through the MCP tool, or a
person's answer to a decision block (749) — and that decision is what this
records: the reason lands on the ticket, so the next reader sees why the
counter was reset rather than finding it mysteriously empty.

A requeue makes a stage ELIGIBLE to run; it does not cause it to run (694).
The caller decides whether to resume the orchestration.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    TicketState,
    UpdateTicketRequest,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.ticket_stage_control import StageControl
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def requeue_artifact_title(stage_key: str) -> str:
    """The title of the record a requeue writes; the reader below finds it by it."""
    return f"Requeued — {stage_key}"


def requeued_since(session: Session, ticket: Ticket, stage_key: str, since: datetime) -> bool:
    """Whether a person requeued `stage_key` after `since`: a decision later than it."""
    return (
        session.exec(
            select(Artifact.id)
            .where(Artifact.ticket_id == ticket.id)
            .where(Artifact.kind == ArtifactKind.CONTEXT)
            .where(Artifact.title == requeue_artifact_title(stage_key))
            .where(Artifact.created_at > since)
            .limit(1)
        ).first()
        is not None
    )


def pending_requeue_reason(session: Session, ticket: Ticket, stage_key: str, run: AgentRun) -> str:
    """The reason a person requeued `stage_key`, for the first run after it; else "".

    A requeue clears `blocking_issues`, the brief's fallback, so without this
    the run it dispatches read nothing of why. lg-milestone-that-1424 was
    requeued on 2026-11-27 naming the one jest test its landing failed, and its
    implementer reported a zero diff, never told. Only the first run after the
    requeue reads it: a later run of the stage was dispatched for its own
    reason, which the requeue does not describe.
    """
    record = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id)
        .where(Artifact.kind == ArtifactKind.CONTEXT)
        .where(Artifact.title == requeue_artifact_title(stage_key))
        .order_by(col(Artifact.created_at).desc())
    ).first()
    if record is None:
        return ""
    later_run = session.exec(
        select(AgentRun.id)
        .where(AgentRun.ticket_id == ticket.id)
        .where(AgentRun.stage_key == stage_key)
        .where(AgentRun.id != run.id)
        .where(AgentRun.created_at > record.created_at)
        .limit(1)
    ).first()
    if later_run is not None:
        return ""
    try:
        rows = json.loads(record.content_json or "{}").get("rows", [])
    except json.JSONDecodeError:
        logger.warning(
            "Unparseable requeue record %s on %s; its reason is not shown to the agent",
            record.id,
            ticket.external_id,
            exc_info=True,
        )
        return ""
    return next((row["v"] for row in rows if row.get("k") == "Reason"), "")


def _blocked_after(session: Session, ticket: Ticket, stage_key: str) -> list[str]:
    """The BLOCKED stages that come after ``stage_key`` in the ticket's workflow.

    Each runs again once ``stage_key`` has, so its block no longer describes
    anything. Left BLOCKED, it held the ticket: requeuing `implement` on a
    ticket blocked at `done` made `implement` pending and the ticket still not
    clear to run (lg-milestone-that-1423). A BLOCKED stage *before* it is a
    separate block and is left for `requeue_outcome` to report.
    """
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    if instance is None or not stages:
        return []
    order = list(parse_stage_map(instance, stages).items())
    keys = [key for key, _ in order]
    if stage_key not in keys:
        return []
    return [
        key for key, status in order[keys.index(stage_key) + 1 :] if status is StageStatus.BLOCKED
    ]


def requeue_stage(
    session: Session,
    orch: StageControl,
    ticket: Ticket,
    *,
    stage_key: str,
    reason: str,
    actor: str,
    state: TicketState = TicketState.BACKLOG,
) -> None:
    """Put `stage_key` back to PENDING with a fresh budget and no block."""
    if not reason.strip():
        raise ValueError("A reason is required — it is the record of why the block was cleared.")
    if not stage_key:
        raise ValueError("This ticket is not on a workflow stage — nothing to requeue.")

    for later in _blocked_after(session, ticket, stage_key):
        orch.update_ticket_manual(
            ticket, UpdateTicketRequest(stage_key=later, stage_status=StageStatus.PENDING)
        )
    orch.update_ticket_manual(
        ticket,
        UpdateTicketRequest(
            stage_key=stage_key,
            stage_status=StageStatus.PENDING,
            state=state,
            auto_state=True,
        ),
    )
    if ticket.workflow_stage_key != stage_key:
        orch.update_ticket_manual(
            ticket,
            UpdateTicketRequest(
                workflow_stage_key=stage_key,
                workflow_stage_status=StageStatus.PENDING,
            ),
        )
    orch.refresh_stage_retry_budget(ticket, stage_key)
    ticket.blocking_issues = ""
    ticket.next_status = ""
    ticket.revision += 1
    ticket.last_updated_by = actor
    ticket.block_kind = None
    session.add(ticket)
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.CONTEXT,
            title=requeue_artifact_title(stage_key),
            content_json=json.dumps(
                {
                    "title": requeue_artifact_title(stage_key),
                    "rows": [
                        {"k": "Stage", "v": stage_key},
                        {"k": "Reason", "v": reason},
                        {"k": "Actor", "v": actor},
                    ],
                }
            ),
        )
    )
    # The artifact is the durable record; the decision is what puts the reason
    # on the history rail, beside the block it cleared.
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.REQUEUED,
        stage_key=stage_key,
        reason=reason,
        evidence={"actor": actor},
    )
    session.commit()
