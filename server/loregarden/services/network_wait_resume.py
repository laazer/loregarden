"""The reconcile step that resumes stages parked on an unreachable provider.

Separate from `network_wait`, which run completion imports: resuming reaches the
orchestration machinery, and run completion sits underneath it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from loregarden.models.domain import Artifact, StageStatus, Ticket
from loregarden.services.network_wait import (
    pending_network_waits,
    provider_reachable,
    stage_of,
)
from loregarden.services.orchestration_recovery import (
    InterruptionResume,
    admit_resumes,
    requeue_and_plan_resume,
    schedule_interrupted_resumes,
)
from sqlmodel import Session

logger = logging.getLogger(__name__)

RESUME_REASON = "The model provider is reachable again after a network outage."


def resume_network_waits(
    session: Session, *, probe: Callable[[], bool] = provider_reachable
) -> list[str]:
    """Requeue and restart every parked stage once the provider answers.

    Probes only when something is waiting, so an idle machine sends nothing.
    A marker whose ticket is no longer blocked on its stage — a person got
    there first — is dropped without touching the ticket.
    """
    markers = pending_network_waits(session)
    if not markers or not probe():
        return []
    plans: list[tuple[Ticket, InterruptionResume]] = []
    for marker in markers:
        plan = _requeue(session, marker)
        session.delete(marker)
        session.commit()
        if plan is not None:
            plans.append(plan)
    requests, handled = admit_resumes(session, plans)
    schedule_interrupted_resumes(requests)
    if handled:
        logger.warning(
            "Network is back; resumed %d parked ticket(s) (%d started now): %s",
            len(handled),
            len(requests),
            ", ".join(handled),
        )
    return handled


def _requeue(session: Session, marker: Artifact) -> tuple[Ticket, InterruptionResume] | None:
    ticket = session.get(Ticket, marker.ticket_id)
    stage_key = stage_of(marker)
    if ticket is None or not _still_parked(ticket, stage_key):
        return None
    plan = requeue_and_plan_resume(session, ticket, stage_key, reason=RESUME_REASON)
    return (ticket, plan) if plan is not None else None


def _still_parked(ticket: Ticket, stage_key: str) -> bool:
    return (
        ticket.workflow_stage_key == stage_key
        and ticket.workflow_stage_status is StageStatus.BLOCKED
    )
