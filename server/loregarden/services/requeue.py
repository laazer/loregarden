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

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    StageStatus,
    Ticket,
    TicketState,
    UpdateTicketRequest,
)
from loregarden.services.ticket_stage_control import StageControl
from sqlmodel import Session


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
            title=f"Requeued — {stage_key}",
            content_json=json.dumps(
                {
                    "title": f"Requeued — {stage_key}",
                    "rows": [{"k": "Stage", "v": stage_key}, {"k": "Reason", "v": reason}],
                }
            ),
        )
    )
    session.commit()
