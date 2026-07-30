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
from loregarden.services.run_interruption import blocked_by_interruption
from loregarden.services.run_service import execute_orchestration_background
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterruptionResume:
    ticket_id: str
    auto_approve: bool
    stop_at_stage_key: str | None


def _execute_resumes(requests: list[InterruptionResume]) -> None:
    for request in requests:
        execute_orchestration_background(
            request.ticket_id,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            auto_approve=request.auto_approve,
            stop_at_stage_key=request.stop_at_stage_key,
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
    """Resume builtin autopilot tickets that startup reconciliation interrupted."""
    callbacks = OrchestrationCallbackService(session)
    requests: list[InterruptionResume] = []
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
        requests.append(
            InterruptionResume(
                ticket_id=ticket.id,
                auto_approve=previous.auto_approve,
                stop_at_stage_key=previous.stop_at_stage_key or None,
            )
        )

    schedule_interrupted_resumes(requests)
    if requests:
        logger.warning(
            "Resuming %d orchestration(s) interrupted by restart: %s",
            len(requests),
            ", ".join(request.ticket_id for request in requests),
        )
    return [request.ticket_id for request in requests]
