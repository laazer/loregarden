"""Admission control for the MCP tools that start work.

Both `loregarden_start_orchestration` and `loregarden_start_stage` need the
same three beats — reserve a slot, start the work, bind the slot to it or give
it back — and inlining that twice pushed `tools.py` past the organization
gate's line cap and its own statement cap. It is one shape, so it lives once.

See `services.queue_admission` for why the gate exists: these tools used to
reach the orchestrator directly, so an agent with MCP access could start
unbounded concurrent work while the queue board showed idle lanes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from loregarden.models.domain import OrchestrationDriver, Ticket, Workspace
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
    stage_key: str | None,
    start: Callable[[], Any],
    driver: str = "",
    max_stages: int | None = None,
    force: bool = False,
) -> tuple[Reservation, Any]:
    """Reserve, run `start`, and release the slot if it raised.

    Returns the reservation and whatever `start` produced, or the reservation
    alone when there was no capacity — the caller checks `admitted` and renders
    `queued_response` in that case. Binding is left to the caller because only
    it knows whether it produced an agent run or an orchestration run.
    """
    admission = QueueAdmissionService(session)
    reservation = (
        admission.reserve_stage(ticket, stage_key=stage_key, force=force)
        if stage_key is not None
        # Carried for the parked case only: an entry is the whole record of the
        # ask by the time a lane reaches it, and a dropped override is a
        # different run from the one requested.
        else admission.reserve_orchestration(ticket, driver=driver, max_stages=max_stages)
    )
    if not reservation.admitted:
        return reservation, None

    try:
        return reservation, start()
    except Exception:
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
    ws = session.get(Workspace, ticket.workspace_id)
    if not ws:
        raise ValueError("Workspace not found")
    profile = resolve_orchestration_profile(ws)
    driver_name = arguments.get("driver") or profile.driver.value
    driver = OrchestrationDriver(driver_name)

    def _start():
        if driver == OrchestrationDriver.BUILTIN_AUTOPILOT:
            return BuiltinOrchestrator(session).execute(
                ticket, profile, max_stages=arguments.get("max_stages")
            )
        if driver == OrchestrationDriver.EXTERNAL_MCP:
            return svc.start_orchestration_run(ticket, driver=driver, profile_slug=profile.slug)
        raise ValueError(f"Unsupported driver for MCP start: {driver_name}")

    reservation, run = run_admitted(
        session,
        ticket,
        stage_key=None,
        start=_start,
        driver=arguments.get("driver") or "",
        max_stages=arguments.get("max_stages"),
    )
    if reservation.admitted:
        reservation.bind(orchestration_run_id=run.id)
    return reservation, run
