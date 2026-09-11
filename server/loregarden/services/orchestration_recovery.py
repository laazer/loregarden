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
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.queue_admission import QueueAdmissionService
from loregarden.services.run_interruption import blocked_by_interruption, interrupted_stage_key
from loregarden.services.run_service import execute_orchestration_background
from loregarden.services.workflow_state import parse_stage_map
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


def _interrupted_stage(session: Session, ticket: Ticket) -> str | None:
    """The stage a restart killed, or None if it cannot be pinned down."""
    orch = OrchestrationService(session)
    instance, stages = orch._resolve_stages(ticket)
    if not instance or not stages:
        return None
    return interrupted_stage_key(session, ticket, parse_stage_map(instance, stages))


def _resume_plan(
    session: Session,
    ticket: Ticket,
    previous: OrchestrationRun | None,
) -> InterruptionResume | None:
    """How to recover this interrupted ticket, or None to leave it for a human.

    The decision is about *who was driving*, because a restart is the control
    plane's own fault and the recovery should hand the work back the way it was
    being done — not promote it to something else.

    - **Builtin autopilot.** Resume the whole drive on its original terms. This
      was the only case handled at all, which is why 52 of the 82 interrupted
      runs measured were unrecoverable: they had no orchestration run behind
      them, so this function's predecessor skipped them and their tickets sat
      blocked under a message telling a human to re-run the stage.
    - **A manual stage run, or no orchestration run at all.** Re-run that one
      stage and stop, by pointing `stop_at_stage_key` at it. Resuming a full
      autopilot drive here would take a ticket somebody ran one stage of and
      run it to completion, which nobody asked for.
    - **An external harness (`EXTERNAL_MCP`).** Not ours to resume: a terminal
      agent is the driver, and starting our own would put two drivers on one
      worktree. Logged rather than silently skipped, so a ticket stranded this
      way is visible instead of merely absent.
    - **Cancelled.** A human stopped it. Restarting would fight the stop.
    """
    if previous is not None and previous.cancel_requested_at is not None:
        return None

    if previous is not None and previous.driver is OrchestrationDriver.BUILTIN_AUTOPILOT:
        return InterruptionResume(
            ticket_id=ticket.id,
            auto_approve=previous.auto_approve,
            stop_at_stage_key=previous.stop_at_stage_key or None,
            timeout_seconds=previous.timeout_override_seconds,
        )

    if previous is not None and previous.driver is OrchestrationDriver.EXTERNAL_MCP:
        logger.warning(
            "Ticket %s was interrupted mid-run under an external harness (%s); leaving it "
            "blocked rather than driving it from here. Re-run the stage from that harness.",
            ticket.external_id,
            previous.run_code,
        )
        return None

    stage_key = _interrupted_stage(session, ticket)
    if not stage_key:
        logger.warning(
            "Ticket %s is blocked by an interruption but no interrupted stage could be "
            "identified; leaving it for a human",
            ticket.external_id,
        )
        return None
    # auto_approve stays off: nobody granted this run unattended approval, and a
    # single stage re-run does not need it.
    return InterruptionResume(
        ticket_id=ticket.id,
        auto_approve=False,
        stop_at_stage_key=stage_key,
        timeout_seconds=previous.timeout_override_seconds if previous else None,
    )


def resume_interrupted_orchestrations(session: Session) -> list[str]:
    """Recover tickets that startup reconciliation found mid-run, each the way it
    was being driven — see `_resume_plan` for which recovery each driver earns.

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
        plan = _resume_plan(session, ticket, previous)
        if plan is None:
            continue

        reservation = admission.reserve_orchestration(
            ticket,
            auto_approve=plan.auto_approve,
            stop_at_stage_key=plan.stop_at_stage_key,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT.value,
            timeout_seconds=plan.timeout_seconds,
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
            auto_approve=plan.auto_approve,
            stop_at_stage_key=plan.stop_at_stage_key or "",
            timeout_override_seconds=plan.timeout_seconds,
        )
        reservation.bind(orchestration_run_id=claim.id)
        requests.append(plan)
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
