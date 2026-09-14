"""Record a choice the orchestrator made, where a person can see it.

The orchestrator makes the most consequential decisions in this control plane —
refusing a dispatch, settling a run it did not start, overruling a workspace
gate — and until lg-workflow-integrity-734 every one of them was a
`logger.warning` to server stdout, which nothing in the UI reads. The ticket
history rail could show that a stage transitioned; it could not show that the
orchestrator had chosen not to let one.

One publisher, so every decision lands with the same shape and the client
renders them all the same way. The log line is kept beside the event on purpose:
the event is for the UI, the log is for the operator at the terminal, and
replacing one with the other loses a reader.
"""

from __future__ import annotations

import logging
from typing import Any

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import EventType, OrchestratorDecision, Ticket
from sqlmodel import Session

logger = logging.getLogger(__name__)


def record_orchestrator_decision(
    session: Session,
    ticket: Ticket,
    *,
    decision: OrchestratorDecision,
    stage_key: str,
    reason: str,
    run_id: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    """Publish the decision as a domain event and log it.

    `reason` is prose a person reads on the history rail, so it should name
    what was decided and why in one sentence. `evidence` is the rows that
    justify it — run codes, the pair, the parent's status — for anyone who
    wants to check the sentence against the data.
    """
    logger.warning(
        "orchestrator %s on %s/%s: %s", decision.value, ticket.external_id, stage_key, reason
    )
    event_bus.publish(
        session,
        EventType.ORCHESTRATOR_DECISION,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_id=run_id,
        payload={
            "decision": decision.value,
            "stage_key": stage_key,
            "reason": reason,
            **(evidence or {}),
        },
    )
