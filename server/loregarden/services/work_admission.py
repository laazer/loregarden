"""Admission control for the operations that start work.

Both `orchestration_start` and `stage_start` need the
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

from collections.abc import Callable
from typing import Any

from loregarden.mcp.shaped_errors import UnknownWorkspaceSlug
from loregarden.models.domain import (
    ExternalHarness,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.queue_admission import QueueAdmissionService, Reservation
from loregarden.services.queue_waiting import place_in_line
from loregarden.services.run_concurrency import OrchestrationAlreadyRunning
from loregarden.services.start_keys import run_for_key, waiting_entry_for_key
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session


class WorkQueued(Exception):  # noqa: N818 — reads as the condition, like StopIteration
    """The machine is full, so the work waits in line rather than starting.

    Not a failure: the work is queued and will run. Each transport says so in
    its own way (MCP replies `status: queued`, REST answers 409), and an agent
    driving this control plane should wait its turn, not learn a new failure.
    """

    def __init__(self, reservation: Reservation, **context: Any) -> None:
        super().__init__(reservation.message or "queued")
        self.reservation = reservation
        self.context = context

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "status": "queued", **self.context, **self.reservation.as_dict()}


class OrchestrationStart(BaseModel):
    """What a caller may ask of an orchestration start, on any transport."""

    model_config = ConfigDict(extra="ignore", use_attribute_docstrings=True)

    ticket_id: str
    """Loregarden ticket UUID."""
    #: None takes the profile's `unattended_driver`: a terminal agent names its own.
    driver: OrchestrationDriver | None = None
    """Orchestration driver."""
    #: An outside harness drives the run; no slot is taken (see `start_external`).
    external_harness: ExternalHarness | None = None
    """Set when a coding harness outside this control plane is driving the ticket. Stamps the run
    and every stage under it with that harness so its results are comparable, and takes the run out
    of the queue — it spawns nothing here, so it waits for no lane.
    """
    #: A retry carrying the same key gets the run its first attempt created.
    idempotency_key: str = ""
    """Optional caller-chosen key. If a start with this key already created a run for this ticket,
    that run is returned instead of a second one, running or finished, and one still waiting in a
    lane reports its place — so a call that failed ambiguously (socket closed, timed out) can be
    retried without re-reading the ticket first. Choose a new key for a genuinely new start;
    reusing one replays. Released only when the start failed before anything ran.
    """
    max_stages: int | None = None
    """Optional cap on stages for builtin autopilot."""
    stop_at_stage_key: str | None = None
    auto_approve: bool = False
    approve_design_plans: bool = True
    auto_repair: bool = True
    timeout_seconds: int | None = None
    #: The lane to run in; None lets the pool pick.
    slot_number: int | None = None


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


def _replay(
    session: Session, ticket: Ticket, idempotency_key: str
) -> tuple[Reservation, OrchestrationRun | None] | None:
    """What an earlier start with this key already got, or None if there was none.

    No slot is taken: a replay spawns nothing, so reserving one would idle
    capacity for work that is already done or running. A start still waiting in
    a lane is answered with its place even when another lane is free; admitting
    it there would run the ticket now and again when its own lane reached it.
    """
    existing = run_for_key(session, ticket.id, idempotency_key)
    if existing is not None:
        return Reservation(admitted=True, message="Replayed by idempotency key"), existing
    waiting = waiting_entry_for_key(session, ticket.id, idempotency_key)
    if waiting is None:
        return None
    return (
        Reservation(
            admitted=False,
            slot_number=waiting.slot_number,
            position=waiting.position,
            message=place_in_line(waiting),
        ),
        None,
    )


def start_orchestration_admitted(
    session: Session, svc, start: OrchestrationStart
) -> tuple[Reservation, Any]:
    """Start a run on the driver named, else the profile's unattended one, gated.

    Mostly admission: resolve the driver, take a slot, dispatch, bind. The
    caller renders the result.
    """
    ticket = svc.resolve_ticket(ticket_id=start.ticket_id)

    # Before anything is spent: a caller retrying an ambiguous failure gets the
    # run its first attempt may or may not have created. Two writes failed this
    # way during one recovery, and both times the only safe move was to re-read
    # the ticket by hand before retrying (lg-workflow-integrity-696).
    idempotency_key = start.idempotency_key.strip()
    replayed = _replay(session, ticket, idempotency_key)
    if replayed is not None:
        return replayed

    ws = workspace_for_ticket(session, ticket)
    if not ws:
        raise UnknownWorkspaceSlug(ticket.external_id)
    profile = resolve_orchestration_profile(ws)
    driver = start.driver or profile.unattended_driver
    if driver not in (OrchestrationDriver.BUILTIN_AUTOPILOT, OrchestrationDriver.EXTERNAL_MCP):
        raise ValueError(
            f"An orchestration cannot start on the {driver.value} driver; start a single stage"
        )
    active = svc.get_active_orchestration_run(ticket.id)
    if active is not None:
        # Before the reservation, so a refused start takes no slot; and before
        # the claim, which would otherwise hand back this live run for the
        # failure path below to abandon.
        raise ValueError(f"Orchestration already running: {active.run_code}")

    run_options = {
        "auto_approve": start.auto_approve,
        "approve_design_plans": start.approve_design_plans,
        "auto_repair": start.auto_repair,
    }
    # The overrides are carried for the parked case too: an entry is the whole
    # record of the ask by the time a lane reaches it, and a dropped override
    # is a different run from the one requested.
    reservation = QueueAdmissionService(session).reserve_orchestration(
        ticket,
        **run_options,
        stop_at_stage_key=start.stop_at_stage_key,
        preferred_slot=start.slot_number,
        driver=start.driver.value if start.driver else "",
        max_stages=start.max_stages,
        timeout_seconds=start.timeout_seconds,
        idempotency_key=idempotency_key,
    )
    if not reservation.admitted:
        return reservation, None

    # Claimed and bound *before* the work starts, not after it returns. The
    # builtin driver runs the whole pipeline inside `execute`, so binding on
    # return bound to a finished run: for the life of the orchestration the
    # slot named nothing, the board drew a lane busy with nothing in it, and
    # the reclaim sweep would have handed the lane to a second ticket (775).
    # `start_orchestration_run` adopts the claim on either driver.
    claim_options = {
        **run_options,
        "stop_at_stage_key": start.stop_at_stage_key or "",
        "timeout_override_seconds": start.timeout_seconds,
        # With the claim, not once the start returns: the builtin driver returns
        # when the pipeline is over, and until then a retry could not find the
        # run it was retrying (lg-workflow-integrity-2797). A claim that fails
        # unstarted gives the key back (`start_keys.release_unstarted`).
        "idempotency_key": idempotency_key,
    }
    try:
        claim = svc.claim_orchestration_run(
            ticket, driver=driver, profile_slug=profile.slug, **claim_options
        )
    except (OrchestrationAlreadyRunning, IntegrityError):
        # Another start of this ticket claimed since the lookups above: the same
        # call's first attempt, if it carried this key.
        session.rollback()
        reservation.release()
        replayed = _replay(session, ticket, idempotency_key)
        if replayed is not None:
            return replayed
        raise
    reservation.bind(orchestration_run_id=claim.id)

    def _start() -> OrchestrationRun:
        if driver == OrchestrationDriver.BUILTIN_AUTOPILOT:
            return BuiltinOrchestrator(session).execute(
                ticket,
                profile,
                max_stages=start.max_stages,
                stop_at_stage_key=start.stop_at_stage_key,
                timeout_seconds=start.timeout_seconds,
                **run_options,
            )
        return svc.start_orchestration_run(
            ticket, driver=driver, profile_slug=profile.slug, **claim_options
        )

    run = _run_on_bound_claim(session, svc, reservation=reservation, claim=claim, start=_start)
    return reservation, run
