"""Admission control: the slot pool is the machine's limit, not the board's.

The lanes bound how much runs at once — but only for work started *from* the
queue board. The Dashboard, the chat primitives and the MCP tools each reached
the orchestrator directly, so an agent driving this control plane could start
unbounded concurrent work while the board showed three idle lanes. The pool
described itself as this machine's capacity and enforced one surface's.

This is the gate those surfaces call:

    reservation = QueueAdmissionService(session).reserve_orchestration(ticket)
    if not reservation.admitted:
        return ...  # parked in a lane; tell the caller where
    run = ...       # the caller starts work exactly as it always did
    reservation.bind(run_id=..., orchestration_run_id=...)

**Reserve, don't dispatch.** An earlier cut had this service start the work
itself through the lane. That quietly dropped whatever each caller passes to
its own start path — `timeout_seconds`, the driver, `max_stages` — because the
lane knows one way to start a thing. Reserving a slot and handing it back keeps
every caller's semantics and still makes the claim real.

**Only outside requests come here.** Promotion, approval-resume and conflict
resolution all continue work that already holds a lane; gating them would
deadlock, since the thing occupying the lane is the thing waiting to proceed.
Interruption recovery after a restart is different: the failed run already
released its lane, so that path admits again rather than resuming in place.
The gate lives at the external entry points rather than inside
`schedule_orchestration` / `schedule_agent_run` for exactly that reason —
bypass flags threaded through the internals would be one missed call site away
from a stall.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loregarden.models.domain import AgentSlot, Ticket

# Imported for its install side effect as much as for anything: admission is the
# gate every externally-started run passes through, so installing the lane
# dispatcher here covers the API, MCP and CLI processes in one place.
from loregarden.services import queue_dispatch  # noqa: F401
from loregarden.services.parallel_queue import claim_free_slot
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.websocket_events import emit_execution_update
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


@dataclass
class Reservation:
    """A claimed slot, or the lane a request was parked in instead."""

    admitted: bool
    slot_number: int | None = None
    position: int | None = None
    message: str = ""
    _session: Session | None = field(default=None, repr=False)
    #: What ``bind`` pointed the slot at, so ``release`` can tell "still mine"
    #: from "someone else's now". A lane that drains between the two — which it
    #: does whenever the caller's start failed and the release of *that* run
    #: started the next entry — hands the slot to a live orchestration, and a
    #: blind release would mark it available while an agent is in it.
    _bound_id: str | None = field(default=None, repr=False)
    #: True when this names a slot the caller's orchestration *already* held
    #: rather than one claimed for it. Such a reservation must never release —
    #: the orchestration still needs the slot for its next stage — and must not
    #: re-bind, because the slot already points where it should
    #: (lg-workflow-integrity-568).
    reused: bool = False

    def bind(self, *, run_id: str | None = None, orchestration_run_id: str | None = None) -> None:
        """Point the reserved slot at what the caller actually started.

        Until this lands the slot is claimed but names nothing, which is what
        stops a second request slipping into it. Callers must either bind or
        release, or the lane stays held by a run that does not exist.

        An admitted request never went through ``add_to_lane``, so without a
        ``QueuedRun`` here the board's history would never record the outcome —
        including a later success after an interruption failure.
        """
        if not self.admitted or self.slot_number is None or self._session is None:
            return
        if self.reused:
            # Already pointing at this orchestration; re-binding would only
            # rewrite the same values and re-emit an update.
            return
        slot = _slot(self._session, self.slot_number)
        if not slot:
            return
        if run_id:
            slot.current_run_id = run_id
        if orchestration_run_id:
            slot.current_orchestration_run_id = orchestration_run_id
        self._bound_id = orchestration_run_id or run_id
        self._session.add(slot)
        if orchestration_run_id:
            QueueLaneService(self._session).ensure_active_entry_for_orchestration(
                self.slot_number, orchestration_run_id
            )
        self._session.commit()
        emit_execution_update()

    def release(self) -> None:
        """Give the slot back — the caller's start failed or did nothing.

        A no-op once the slot has moved on to something else: releasing a lane
        that a *different* orchestration now holds would report an occupied lane
        as available and let the pool admit past its own limit.
        """
        if not self.admitted or self.slot_number is None or self._session is None:
            return
        if self.reused:
            # Releasing here would take the slot away from an orchestration that
            # is still running — this reservation never owned it. A failed stage
            # start on a reused slot leaves the orchestration exactly as it was.
            return
        slot = _slot(self._session, self.slot_number)
        if not slot:
            return
        occupant = slot.current_orchestration_run_id or slot.current_run_id
        if occupant and occupant != self._bound_id:
            return
        slot.is_available = True
        slot.current_run_id = None
        slot.current_orchestration_run_id = None
        slot.released_at = datetime.now(timezone.utc)
        self._session.add(slot)
        self._session.commit()
        emit_execution_update()

    def as_dict(self) -> dict:
        return {
            "admitted": self.admitted,
            "slot_number": self.slot_number,
            "position": self.position,
            "message": self.message,
        }


def _slot(session: Session, slot_number: int) -> AgentSlot | None:
    return session.exec(select(AgentSlot).where(AgentSlot.slot_number == slot_number)).first()


class QueueAdmissionService:
    def __init__(self, session: Session, max_concurrent: int = 3) -> None:
        self.session = session
        self.lanes = QueueLaneService(session, max_concurrent=max_concurrent)

    def reserve_orchestration(
        self,
        ticket: Ticket,
        *,
        auto_approve: bool = False,
        stop_at_stage_key: str | None = None,
        preferred_slot: int | None = None,
        driver: str = "",
        max_stages: int | None = None,
        timeout_seconds: int | None = None,
    ) -> Reservation:
        """A slot to run the whole ticket in, or a place in line.

        `driver`, `max_stages` and `timeout_seconds` are carried only so a
        *parked* request still runs what was asked for. An admitted one never
        needs them: the caller starts the work itself, which is the point of
        reserving rather than dispatching.
        """
        return self._reserve(
            ticket,
            entry_kind="orchestration",
            stage_key="",
            auto_approve=auto_approve,
            stop_at_stage_key=stop_at_stage_key,
            preferred_slot=preferred_slot,
            driver=driver,
            max_stages=max_stages,
            timeout_seconds=timeout_seconds,
        )

    def reserve_stage(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
        auto_approve: bool = False,
        preferred_slot: int | None = None,
        timeout_seconds: int | None = None,
        force: bool = False,
        orchestration_run_id: str = "",
    ) -> Reservation:
        """A slot to run one stage in, or a place in line.

        Parked as a stage rather than an orchestration: "run this one stage" and
        "run everything left" are different requests, and silently promoting one
        to the other would be a surprise measured in agent-hours.

        `orchestration_run_id` is the run this stage belongs to, when it belongs
        to one. An externally driven orchestration starts its stages one at a
        time through this path, and each start used to claim a *new* slot while
        the orchestration still held the one it was admitted with — so a
        12-stage ticket exhausted a 3-slot pool at its second stage and held the
        surplus against nothing. The pool limits concurrent work, and stages run
        sequentially inside one orchestration, so that orchestration is one
        occupant however many stages it starts (lg-workflow-integrity-568).
        """
        return self._reserve(
            ticket,
            entry_kind="stage",
            stage_key=stage_key or "",
            auto_approve=auto_approve,
            stop_at_stage_key=None,
            preferred_slot=preferred_slot,
            timeout_seconds=timeout_seconds,
            force=force,
            orchestration_run_id=orchestration_run_id,
        )

    # ---- internals -----------------------------------------------------

    def _reserve(
        self,
        ticket: Ticket,
        *,
        entry_kind: str,
        stage_key: str,
        auto_approve: bool,
        stop_at_stage_key: str | None,
        preferred_slot: int | None = None,
        driver: str = "",
        max_stages: int | None = None,
        timeout_seconds: int | None = None,
        force: bool = False,
        orchestration_run_id: str = "",
    ) -> Reservation:
        self.lanes.slots.initialize_slots()

        held = self._slot_held_by(orchestration_run_id)
        if held is not None:
            # This orchestration is already inside the pool. Handing it a second
            # slot is what let one run hold two, which is also why releasing by
            # run id could not tell them apart — the ambiguity is removed by not
            # creating it (lg-workflow-integrity-568).
            return Reservation(
                admitted=True,
                slot_number=held.slot_number,
                message=f"Running in slot {held.slot_number}",
                _session=self.session,
                _bound_id=orchestration_run_id,
                reused=True,
            )

        # Claimed before the caller starts anything, and claimed atomically: the
        # select-then-mutate this replaced let two requests arriving together
        # both read the same row as free and both write it. `preferred_slot` is
        # a preference, not a demand — an operator whose chosen lane filled
        # between opening the dialog and confirming wants the ticket to run, and
        # the slot number is presentation.
        free = claim_free_slot(self.session, preferred=preferred_slot)

        if free:
            return Reservation(
                admitted=True,
                slot_number=free.slot_number,
                message=f"Started in slot {free.slot_number}",
                _session=self.session,
            )

        # Full: wait in the lane the operator chose, or the shortest.
        lane = preferred_slot if preferred_slot in self.lanes.lane_numbers() else None
        if lane is None:
            lane = self._shortest_lane()
        result = self.lanes.add_to_lane(
            ticket_id=ticket.id,
            slot_number=lane,
            auto_approve=auto_approve,
            stop_at_stage_key=stop_at_stage_key,
            entry_kind=entry_kind,
            stage_key=stage_key,
            driver=driver,
            max_stages=max_stages,
            timeout_seconds=timeout_seconds,
            force=force,
        )
        position = result.get("position")
        logger.info(
            "Admission queued %s for ticket %s in lane %d at position %s",
            entry_kind,
            ticket.id,
            lane,
            position,
        )
        return Reservation(
            admitted=False,
            slot_number=lane,
            position=position,
            message=(
                f"All {self.lanes.max_concurrent} execution slots are busy. "
                f"Queued in slot {lane} at position {position}."
            ),
            _session=self.session,
        )

    def _slot_held_by(self, orchestration_run_id: str) -> AgentSlot | None:
        """The slot this orchestration already occupies, if any."""
        if not orchestration_run_id:
            return None
        return self.session.exec(
            select(AgentSlot).where(AgentSlot.current_orchestration_run_id == orchestration_run_id)
        ).first()

    def _shortest_lane(self) -> int:
        """The lane with the least waiting behind it, ties going to the lowest.

        Shortest rather than round-robin: the queue is a wait, and the honest
        default is the lane that starts soonest.
        """
        lanes = self.lanes.lane_numbers()
        if not lanes:
            raise ValueError("No execution slots are configured")
        return min(lanes, key=lambda number: (len(self.lanes.waiting_in_lane(number)), number))
