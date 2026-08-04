"""Execution lanes: one serial pipeline per slot.

A slot used to be a place a single ticket sat, with one shared waiting line
behind all of them — so "run this after that one" could not be said at all. A
lane is now a pipeline you feed: drop a ticket in and it runs if the lane is
idle, otherwise it waits its turn behind whatever is already there, and starts
on its own the moment the lane frees.

Two consequences shape everything here:

**Adding is committing.** There is no staging step. A queued entry has to start
by itself when the lane drains, which only the server can do — a plan held in
the browser cannot.

**A lane runs a whole ticket.** Entries dispatch an *orchestration*, not a
single stage, so a lane is occupied for the life of a pipeline that spans many
agent runs. That is why the slot names an orchestration run rather than an
agent run: releasing on the first agent run to finish would hand the lane away
mid-ticket.

Capacity is still shared and global — the lanes are this machine's, not a
workspace's (see `parallel_queue`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    OrchestrationRun,
    QueuedRun,
    QueuePosition,
    Ticket,
)
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.websocket_events import emit_execution_update
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Statuses that mean "still waiting in a lane".
WAITING_STATUSES = (QueuePosition.QUEUED, QueuePosition.SCHEDULED)


class QueueLaneService:
    """Add, order and drain the per-slot pipelines."""

    def __init__(self, session: Session, max_concurrent: int = 3) -> None:
        self.session = session
        self.max_concurrent = max_concurrent
        self.slots = ParallelQueueService(session, max_concurrent=max_concurrent)

    # ---- reading -------------------------------------------------------

    def lane_numbers(self) -> list[int]:
        self.slots.initialize_slots()
        rows = self.session.exec(select(AgentSlot).order_by(AgentSlot.slot_number)).all()
        return [slot.slot_number for slot in rows]

    def waiting_in_lane(self, slot_number: int) -> list[QueuedRun]:
        stmt = (
            select(QueuedRun)
            .where(
                (QueuedRun.slot_number == slot_number) & (QueuedRun.status.in_(WAITING_STATUSES))
            )
            .order_by(QueuedRun.position)
        )
        return list(self.session.exec(stmt).all())

    # ---- writing -------------------------------------------------------

    def add_to_lane(
        self,
        *,
        ticket_id: str,
        slot_number: int,
        auto_approve: bool = False,
        stop_at_stage_key: str | None = None,
        entry_kind: str = "orchestration",
        stage_key: str = "",
    ) -> dict:
        """Put a ticket in a lane, starting it if the lane is idle.

        `entry_kind` is "orchestration" (run the whole ticket, what the board
        does) or "stage" (run one stage, what the Dashboard and MCP ask for).
        Parking a stage request as an orchestration would silently turn "run
        this one stage" into "run everything left", so the entry says which.

        Returns ``{"status": "started"|"queued", "position": int, ...}``.
        """
        ticket = self.session.get(Ticket, ticket_id)
        if not ticket:
            raise ValueError("Ticket not found")

        self.slots.initialize_slots()
        slot = self._slot(slot_number)
        if not slot:
            raise ValueError(f"No such execution slot: {slot_number}")

        waiting = self.waiting_in_lane(slot_number)
        entry = QueuedRun(
            id=str(uuid4()),
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            slot_number=slot_number,
            position=len(waiting) + 1,
            status=QueuePosition.QUEUED,
            # Carried on the entry so the lane honours them whenever it starts,
            # which may be long after the dialog that set them is gone.
            auto_approve=auto_approve,
            stop_at_stage_key=stop_at_stage_key or "",
            entry_kind=entry_kind,
            stage_key=stage_key,
        )
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)

        if slot.is_available:
            started = self.start_lane_head(slot_number)
            if started:
                emit_execution_update()
                return {
                    "status": "started",
                    "slot_number": slot_number,
                    "entry_id": entry.id,
                    "message": f"Started in slot {slot_number}",
                }

        emit_execution_update()
        return {
            "status": "queued",
            "slot_number": slot_number,
            "entry_id": entry.id,
            "position": entry.position,
            "message": f"Queued at position {entry.position} in slot {slot_number}",
        }

    def start_lane_head(self, slot_number: int) -> QueuedRun | None:
        """Dispatch the front of a lane, if the lane is free and has one."""
        slot = self._slot(slot_number)
        if not slot or not slot.is_available:
            return None

        head = next(iter(self.waiting_in_lane(slot_number)), None)
        if not head:
            return None

        ticket = self.session.get(Ticket, head.ticket_id)
        if not ticket:
            # The ticket went away while it waited. Drop the entry rather than
            # wedging the lane on something that can never run.
            logger.warning("Dropping lane entry %s: ticket %s is gone", head.id, head.ticket_id)
            self.session.delete(head)
            self.session.commit()
            self._renumber(slot_number)
            return self.start_lane_head(slot_number)

        if head.entry_kind == "stage":
            agent_run = self._dispatch_stage(ticket, head)
            if agent_run is None:
                return None
            slot.is_available = False
            slot.current_run_id = agent_run.id
            head.run_id = agent_run.id
            orch_run = None
        else:
            orch_run = self._dispatch_orchestration(
                ticket,
                auto_approve=head.auto_approve,
                stop_at_stage_key=head.stop_at_stage_key or None,
            )
            if orch_run is None:
                # Dispatch refused (already orchestrating, no workflow). Leave
                # the entry in place rather than claiming the lane for nothing.
                return None
            slot.is_available = False
            slot.current_orchestration_run_id = orch_run.id
            head.orchestration_run_id = orch_run.id
        slot.assigned_at = datetime.now(timezone.utc)
        head.status = QueuePosition.ACTIVE
        head.promoted_at = datetime.now(timezone.utc)
        head.started_at = datetime.now(timezone.utc)
        self.session.add(slot)
        self.session.add(head)
        self.session.commit()
        self._renumber(slot_number)

        logger.info(
            "Lane %d started %s for ticket %s",
            slot_number,
            head.entry_kind,
            ticket.id,
        )
        return head

    def on_orchestration_complete(self, orchestration_run_id: str) -> None:
        """Free whichever lane held this orchestration, then start its next entry.

        The lane is identified by the orchestration rather than by any single
        agent run: a ticket's pipeline spans many, and releasing on the first to
        finish would hand the lane away mid-ticket.
        """
        slot = self.session.exec(
            select(AgentSlot).where(AgentSlot.current_orchestration_run_id == orchestration_run_id)
        ).first()
        if not slot:
            return

        entry = self.session.exec(
            select(QueuedRun).where(QueuedRun.orchestration_run_id == orchestration_run_id)
        ).first()
        if entry:
            entry.status = QueuePosition.STARTED
            self.session.add(entry)

        slot_number = slot.slot_number
        slot.is_available = True
        slot.current_orchestration_run_id = None
        slot.current_run_id = None
        slot.released_at = datetime.now(timezone.utc)
        self.session.add(slot)
        self.session.commit()

        self.start_lane_head(slot_number)
        emit_execution_update()

    def remove_entry(self, entry_id: str) -> bool:
        """Take a waiting entry out of its lane. Running entries are untouched."""
        entry = self.session.get(QueuedRun, entry_id)
        if not entry or entry.status not in WAITING_STATUSES:
            return False
        slot_number = entry.slot_number
        self.session.delete(entry)
        self.session.commit()
        self._renumber(slot_number)
        emit_execution_update()
        return True

    def move_entry(self, entry_id: str, *, slot_number: int, position: int) -> bool:
        """Move a waiting entry within its lane, or into another one."""
        entry = self.session.get(QueuedRun, entry_id)
        if not entry or entry.status not in WAITING_STATUSES:
            return False
        if not self._slot(slot_number):
            return False

        from_lane = entry.slot_number
        entry.slot_number = slot_number
        # Out of the way first, so renumbering the target lane cannot collide
        # with the position this entry still holds.
        entry.position = 10_000 + max(0, position)
        self.session.add(entry)
        self.session.commit()

        if from_lane != slot_number:
            self._renumber(from_lane)
        self._renumber(slot_number, insert_at=(entry.id, position))

        # A lane that was idle should pick this up rather than sit on it.
        target = self._slot(slot_number)
        if target and target.is_available:
            self.start_lane_head(slot_number)
        emit_execution_update()
        return True

    # ---- internals -----------------------------------------------------

    def _slot(self, slot_number: int) -> AgentSlot | None:
        return self.session.exec(
            select(AgentSlot).where(AgentSlot.slot_number == slot_number)
        ).first()

    def _renumber(self, slot_number: int, insert_at: tuple[str, int] | None = None) -> None:
        """Close gaps in a lane so positions read 1..N with no holes."""
        waiting = self.waiting_in_lane(slot_number)
        if insert_at:
            entry_id, target = insert_at
            moved = next((e for e in waiting if e.id == entry_id), None)
            if moved:
                waiting = [e for e in waiting if e.id != entry_id]
                index = max(0, min(len(waiting), target - 1))
                waiting.insert(index, moved)

        for index, entry in enumerate(waiting, start=1):
            entry.position = index
            self.session.add(entry)
        self.session.commit()

    def _dispatch_stage(self, ticket: Ticket, entry: QueuedRun) -> AgentRun | None:
        """Start one stage and return the run that now owns the lane.

        The single-stage twin of `_dispatch_orchestration`, for entries parked
        by admission control on behalf of the Dashboard or MCP. A lane holding
        one of these is released by `complete_run_tail` (which frees whatever
        slot names the finished run) rather than by `complete_orchestration`.
        """
        from loregarden.services.orchestration import OrchestrationService
        from loregarden.services.run_service import schedule_agent_run

        try:
            run = OrchestrationService(self.session).start_run(
                ticket,
                stage_key=entry.stage_key or None,
                auto_approve=entry.auto_approve,
            )
        except ValueError as exc:
            logger.warning("Lane stage dispatch failed for ticket %s: %s", ticket.id, exc)
            return None

        schedule_agent_run(run.id)
        return run

    def _dispatch_orchestration(
        self,
        ticket: Ticket,
        *,
        auto_approve: bool,
        stop_at_stage_key: str | None,
    ) -> OrchestrationRun | None:
        """Start the ticket's pipeline and return the run that now owns the lane.

        Imported at call time: run_service reaches orchestration, which reaches
        this module, so a module-level import would close the cycle.
        """
        from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
        from loregarden.services.run_service import schedule_orchestration

        active = OrchestrationCallbackService(self.session).get_active_orchestration_run(ticket.id)
        if active:
            logger.info(
                "Lane dispatch skipped for ticket %s: %s is already orchestrating",
                ticket.id,
                active.run_code,
            )
            return active

        try:
            schedule_orchestration(
                ticket.id,
                auto_approve=auto_approve,
                stop_at_stage_key=stop_at_stage_key or None,
            )
        except ValueError as exc:
            logger.warning("Lane dispatch failed for ticket %s: %s", ticket.id, exc)
            return None

        # `schedule_orchestration` creates the row on the worker; re-read rather
        # than assuming, so the lane binds to a run that actually exists.
        return OrchestrationCallbackService(self.session).get_active_orchestration_run(ticket.id)
