"""Admission control for the MCP tools that start work.

Both `loregarden_start_orchestration` and `loregarden_start_stage` need the
same three beats — reserve a slot, start the work, bind the slot to it or give
it back — and inlining that twice pushed `tools.py` past the organization
gate's line cap and its own statement cap. They differ in one place: a stage
binds to what `start` produced, an orchestration claims its run and binds to
it before `start`, because the builtin driver does not return until the run
is over.

See `services.queue_admission` for why the gate exists: these tools used to
reach the orchestrator directly, so an agent with MCP access could start
unbounded concurrent work while the queue board showed idle lanes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlmodel import Session, col, select

from loregarden.models.domain import (
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.queue_admission import QueueAdmissionService, Reservation


def queued_response(reservation: Reservation, **extra: Any) -> str:
    """What an MCP caller gets when the machine is full.

    Not an error: the work is queued and will run. An agent driving this
    control plane should wait its turn, not learn a new failure mode.
    """
    return json.dumps(
        {"ok": False, "status": "queued", **extra, **reservation.as_dict()},
        indent=2,
    )


def run_admitted(
    session: Session,
    ticket: Ticket,
    *,
    stage_key: str,
    start: Callable[[], Any],
    force: bool = False,
    orchestration_run_id: str = "",
) -> tuple[Reservation, Any]:
    """Reserve a stage's slot, run `start`, and release the slot if it raised.

    Returns the reservation and whatever `start` produced, or the reservation
    alone when there was no capacity — the caller checks `admitted` and renders
    `queued_response` in that case. Binding is left to the caller because only
    it knows whether it produced an agent run or an orchestration run.

    Stages only. An orchestration start binds *before* it runs (see
    `start_orchestration_admitted`), which this reserve-start-bind shape cannot
    express.
    """
    admission = QueueAdmissionService(session)
    reservation = admission.reserve_stage(
        ticket,
        stage_key=stage_key,
        force=force,
        # The orchestration this stage belongs to, so an externally driven
        # run reuses the slot it was admitted with instead of claiming one
        # per stage (lg-workflow-integrity-568).
        orchestration_run_id=orchestration_run_id,
    )
    if not reservation.admitted:
        return reservation, None

    try:
        return reservation, start()
    except Exception:
        reservation.release()
        raise


def _run_on_bound_claim(
    session: Session,
    svc,
    *,
    reservation: Reservation,
    claim: OrchestrationRun,
    start: Callable[[], OrchestrationRun],
) -> OrchestrationRun:
    """Run `start` against a claim the slot already names; undo both if it raises.

    The claim is abandoned only while it is still a claim. A builtin run that
    adopted it and then failed has already reached its own terminal status,
    and rewriting that would erase what actually happened.
    """
    try:
        return start()
    except Exception as exc:
        session.rollback()
        session.refresh(claim)
        if claim.status == OrchestrationRunStatus.QUEUED:
            # Never adopted, so nothing else will ever finish it.
            svc.abandon_claim(claim, message=str(exc))
        reservation.release()
        raise


def start_orchestration_admitted(
    session: Session, svc, arguments: dict[str, Any]
) -> tuple[Reservation, Any]:
    """Start a run on whichever driver the workspace profile selects, gated.

    Lives here rather than in `tools.py` because it is now mostly admission:
    resolve the driver, take a slot, dispatch, bind. The caller renders the
    result, which keeps this module from importing back into `tools`.
    """
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])

    # Before anything is spent: a caller retrying an ambiguous failure gets the
    # run its first attempt may or may not have created. Two writes failed this
    # way during one recovery, and both times the only safe move was to re-read
    # the ticket by hand before retrying (lg-workflow-integrity-696).
    idempotency_key = (arguments.get("idempotency_key") or "").strip()
    if idempotency_key:
        existing = session.exec(
            select(OrchestrationRun).where(
                col(OrchestrationRun.ticket_id) == ticket.id,
                col(OrchestrationRun.idempotency_key) == idempotency_key,
            )
        ).first()
        if existing is not None:
            # No slot is taken: a replay spawns nothing, so reserving one
            # would idle capacity for work that is already done or running.
            return Reservation(admitted=True, message="Replayed by idempotency key"), existing

    ws = session.get(Workspace, ticket.workspace_id)
    if not ws:
        raise ValueError("Workspace not found")
    profile = resolve_orchestration_profile(ws)
    driver_name = arguments.get("driver") or profile.driver.value
    driver = OrchestrationDriver(driver_name)
    if driver not in (OrchestrationDriver.BUILTIN_AUTOPILOT, OrchestrationDriver.EXTERNAL_MCP):
        raise ValueError(f"Unsupported driver for MCP start: {driver_name}")
    active = svc.get_active_orchestration_run(ticket.id)
    if active is not None:
        # Before the reservation, so a refused start takes no slot; and before
        # the claim, which would otherwise hand back this live run for the
        # failure path below to abandon.
        raise ValueError(f"Orchestration already running: {active.run_code}")

    admission = QueueAdmissionService(session)
    # Carried for the parked case only: an entry is the whole record of the ask
    # by the time a lane reaches it, and a dropped override is a different run
    # from the one requested.
    reservation = admission.reserve_orchestration(
        ticket, driver=arguments.get("driver") or "", max_stages=arguments.get("max_stages")
    )
    if not reservation.admitted:
        return reservation, None

    # Claimed and bound *before* the work starts, not after it returns. The
    # builtin driver runs the whole pipeline inside `execute`, so binding on
    # return bound to a finished run: for the life of the orchestration the
    # slot named nothing, the board drew a lane busy with nothing in it, and
    # the reclaim sweep would have handed the lane to a second ticket (775).
    # `start_orchestration_run` adopts the claim on either driver.
    claim = svc.claim_orchestration_run(ticket, driver=driver, profile_slug=profile.slug)
    reservation.bind(orchestration_run_id=claim.id)

    def _start() -> OrchestrationRun:
        if driver == OrchestrationDriver.BUILTIN_AUTOPILOT:
            return BuiltinOrchestrator(session).execute(
                ticket, profile, max_stages=arguments.get("max_stages")
            )
        return svc.start_orchestration_run(ticket, driver=driver, profile_slug=profile.slug)

    run = _run_on_bound_claim(session, svc, reservation=reservation, claim=claim, start=_start)
    if idempotency_key and not run.idempotency_key:
        # Stamped after the run exists, so a start that raised leaves no key
        # claiming a run that was never created.
        run.idempotency_key = idempotency_key
        session.add(run)
        session.commit()
    return reservation, run
