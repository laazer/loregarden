from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

from loregarden.models.domain import (
    OrchestrationDriver,
    OrchestrationRun,
    StageStatus,
    Ticket,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.queue_admission import QueueAdmissionService
from loregarden.services.run_interruption import blocked_by_interruption
from loregarden.services.run_service import execute_orchestration_background
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterruptionResume:
    ticket_id: str
    auto_approve: bool
    stop_at_stage_key: str | None
    timeout_seconds: int | None = None


def _execute_resumes(requests: list[InterruptionResume]) -> None:
    for request in requests:
        execute_orchestration_background(
            request.ticket_id,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            auto_approve=request.auto_approve,
            stop_at_stage_key=request.stop_at_stage_key,
            timeout_seconds=request.timeout_seconds,
        )


def schedule_interrupted_resumes(requests: list[InterruptionResume]) -> None:
    """Resume tickets serially so orchestrators cannot share a working tree."""
    if not requests:
        return
    if os.environ.get("LOREGARDEN_SYNC_ORCHESTRATION") == "1":
        _execute_resumes(requests)
        return
    thread = threading.Thread(
        target=_execute_resumes,
        args=(requests,),
        name="loregarden-interruption-recovery",
        daemon=True,
    )
    thread.start()


def resume_interrupted_orchestrations(session: Session) -> list[str]:
    """Resume builtin autopilot tickets that startup reconciliation interrupted.

    Goes through the slot pool: a restart already released whatever lane the
    failed run held, so resuming outside admission left agents running while
    the board showed three idle slots.
    """
    callbacks = OrchestrationCallbackService(session)
    admission = QueueAdmissionService(session)
    requests: list[InterruptionResume] = []
    handled: list[str] = []
    candidates = session.exec(
        select(Ticket).where(Ticket.workflow_stage_status == StageStatus.BLOCKED)
    ).all()

    for ticket in candidates:
        if not blocked_by_interruption(ticket):
            continue
        if callbacks.get_active_orchestration_run(ticket.id):
            continue
        previous = session.exec(
            select(OrchestrationRun)
            .where(OrchestrationRun.ticket_id == ticket.id)
            .order_by(OrchestrationRun.created_at.desc())
        ).first()
        if (
            previous is None
            or previous.driver != OrchestrationDriver.BUILTIN_AUTOPILOT
            or previous.cancel_requested_at is not None
        ):
            continue

        reservation = admission.reserve_orchestration(
            ticket,
            auto_approve=previous.auto_approve,
            stop_at_stage_key=previous.stop_at_stage_key or None,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT.value,
            timeout_seconds=previous.timeout_override_seconds,
        )
        if not reservation.admitted:
            # Parked; the lane will start it when capacity frees.
            logger.info(
                "Interrupted ticket %s parked in lane %s (pool full)",
                ticket.id,
                reservation.slot_number,
            )
            handled.append(ticket.id)
            continue

        claim = callbacks.claim_orchestration_run(
            ticket,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            auto_approve=previous.auto_approve,
            stop_at_stage_key=previous.stop_at_stage_key or "",
            timeout_override_seconds=previous.timeout_override_seconds,
        )
        reservation.bind(orchestration_run_id=claim.id)
        requests.append(
            InterruptionResume(
                ticket_id=ticket.id,
                auto_approve=previous.auto_approve,
                stop_at_stage_key=previous.stop_at_stage_key or None,
                timeout_seconds=previous.timeout_override_seconds,
            )
        )
        handled.append(ticket.id)

    schedule_interrupted_resumes(requests)
    if handled:
        logger.warning(
            "Resuming %d orchestration(s) interrupted by restart (%d scheduled now): %s",
            len(handled),
            len(requests),
            ", ".join(handled),
        )
    return handled
