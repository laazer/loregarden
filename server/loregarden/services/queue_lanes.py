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
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    OrchestrationRun,
    QueuedRun,
    QueuePosition,
    Ticket,
)
from loregarden.services.drain import is_draining, stamp_refusal
from loregarden.services.parallel_queue import (
    LIVE_ORCHESTRATION_STATUSES,
    LIVE_RUN_STATUSES,
    WAITING_STATUSES,
    ParallelQueueService,
    claim_lane_slot,
    release_slot,
)
from loregarden.websocket_events import emit_execution_update
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

__all__ = [
    "WAITING_STATUSES",
    "LaneDispatcher",
    "QueueLaneService",
    "set_lane_dispatcher_factory",
]


class LaneDispatcher(Protocol):
    """Starts the work a lane has reached.

    Declared here and implemented above (`queue_dispatch`), because starting a
    ticket needs the orchestrator and the orchestrator needs the queue. A lane
    that imported its own dispatcher would close that loop; one that calls
    through this protocol does not know what starts the work, only that
    something does.
    """

    def dispatch_stage(self, ticket: Ticket, entry: QueuedRun) -> AgentRun | None: ...

    def dispatch_orchestration(
        self,
        ticket: Ticket,
        *,
        auto_approve: bool,
        stop_at_stage_key: str | None,
        driver: str = "",
        max_stages: int | None = None,
        timeout_seconds: int | None = None,
    ) -> OrchestrationRun | None: ...


#: Set by `queue_dispatch` on import. Resolved when a service is built rather
#: than when this module is, which is the whole reason the cycle is gone.
_dispatcher_factory: Callable[[Session], LaneDispatcher] | None = None


def set_lane_dispatcher_factory(factory: Callable[[Session], LaneDispatcher] | None) -> None:
    """Install what lanes use to start work. Called by the composition root."""
    global _dispatcher_factory  # noqa: PLW0603 — one process-wide wiring point
    _dispatcher_factory = factory


class QueueLaneService:
    """Add, order and drain the per-slot pipelines."""

    def __init__(
        self,
        session: Session,
        max_concurrent: int = 3,
        dispatcher: LaneDispatcher | None = None,
    ) -> None:
        self.session = session
        self.max_concurrent = max_concurrent
        self.slots = ParallelQueueService(session, max_concurrent=max_concurrent)
        if dispatcher is None and _dispatcher_factory is not None:
            dispatcher = _dispatcher_factory(session)
        self.dispatcher = dispatcher

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
        driver: str = "",
        max_stages: int | None = None,
        timeout_seconds: int | None = None,
    ) -> dict:
        """Put a ticket in a lane, starting it if the lane is idle.

        `entry_kind` is "orchestration" (run the whole ticket, what the board
        does) or "stage" (run one stage, what the Dashboard and MCP ask for).
        Parking a stage request as an orchestration would silently turn "run
        this one stage" into "run everything left", so the entry says which.

        `driver` and `max_stages` ride along for the same reason: by the time a
        lane reaches this entry the request that made it is long gone, and an
        override dropped here is a different run from the one that was asked
        for — one that depends on how busy the box happened to be.

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
            driver=driver or "",
            max_stages=max_stages,
            timeout_seconds=timeout_seconds,
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
        """Dispatch the front of a lane, if the lane is free and has one.

        The lane is claimed *before* the dispatch, atomically, and given back on
        every path that does not start work. It used to be the other way round —
        check `is_available`, run the whole dispatch, then write — which left a
        window a concurrent admission could claim the same slot inside, putting
        two live occupants in one lane. The old ordering was protecting a real
        property, that a refused dispatch must not strand a lane, so the release
        below is what makes claiming first safe.
        """
        if is_draining():
            # Before the claim: taking a lane for work that will not start is
            # the strand this path was rewritten to avoid.
            head = next(iter(self.waiting_in_lane(slot_number)), None)
            if head is not None:
                stamp_refusal(self.session, head)
            return None

        slot = claim_lane_slot(self.session, slot_number)
        if slot is None:
            return None

        head = next(iter(self.waiting_in_lane(slot_number)), None)
        if not head:
            release_slot(self.session, slot)
            return None

        if self.dispatcher is None:
            # Loud, because the failure it replaces is the worst kind this queue
            # has: a lane that looks healthy and silently starts nothing. Only
            # reachable in a process that never installed a dispatcher.
            logger.error(
                "Lane %d has work waiting but no dispatcher is installed; "
                "import loregarden.services.queue_dispatch in this entry point",
                slot_number,
            )
            release_slot(self.session, slot)
            return None

        ticket = self.session.get(Ticket, head.ticket_id)
        if not ticket:
            # The ticket went away while it waited. Drop the entry rather than
            # wedging the lane on something that can never run. The lane goes
            # back first — the retry below claims it again from scratch, and a
            # claim held across it would refuse itself.
            logger.warning("Dropping lane entry %s: ticket %s is gone", head.id, head.ticket_id)
            self.session.delete(head)
            self.session.commit()
            self._renumber(slot_number)
            release_slot(self.session, slot)
            return self.start_lane_head(slot_number)

        if head.entry_kind == "stage":
            agent_run = self.dispatcher.dispatch_stage(ticket, head)
            if agent_run is None:
                release_slot(self.session, slot)
                return None
            slot.current_run_id = agent_run.id
            head.run_id = agent_run.id
            orch_run = None
        else:
            orch_run = self.dispatcher.dispatch_orchestration(
                ticket,
                auto_approve=head.auto_approve,
                stop_at_stage_key=head.stop_at_stage_key or None,
                driver=head.driver or "",
                max_stages=head.max_stages,
                timeout_seconds=head.timeout_seconds,
            )
            if orch_run is None:
                # Dispatch refused (already orchestrating, no workflow). Give the
                # lane back and leave the entry in place, so 439's idle-lane
                # retry picks it up rather than the lane sitting held by nothing.
                release_slot(self.session, slot)
                return None
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

    def ensure_active_entry_for_orchestration(
        self, slot_number: int, orchestration_run_id: str
    ) -> QueuedRun | None:
        """Make sure a lane entry exists for an orchestration already on a slot.

        Immediate admission (`reserve` + `bind`) and orphan-heal claim a slot
        without going through ``add_to_lane``. History is read from ``queued_runs``,
        so without an entry the board can show Failed for an interruption and
        then never record the ticket's later success.
        """
        existing = self.session.exec(
            select(QueuedRun).where(QueuedRun.orchestration_run_id == orchestration_run_id)
        ).first()
        if existing:
            return existing

        orch = self.session.get(OrchestrationRun, orchestration_run_id)
        if not orch:
            return None

        now = datetime.now(timezone.utc)
        entry = QueuedRun(
            id=str(uuid4()),
            workspace_id=orch.workspace_id,
            ticket_id=orch.ticket_id,
            orchestration_run_id=orch.id,
            slot_number=slot_number,
            position=1,
            status=QueuePosition.ACTIVE,
            entry_kind="orchestration",
            promoted_at=now,
            started_at=orch.started_at or now,
        )
        self.session.add(entry)
        return entry

    def on_orchestration_complete(self, orchestration_run_id: str) -> None:
        """Free whichever lane held this orchestration, then start its next entry.

        The lane is identified by the orchestration rather than by any single
        agent run: a ticket's pipeline spans many, and releasing on the first to
        finish would hand the lane away mid-ticket.

        The entry is settled whether or not a slot still names this orchestration.
        Those are two records of one release, and `reconcile_slots` reclaims a slot
        the moment its occupant goes terminal — so on any path where it ran first
        (a restart, a status read racing this call) the slot lookup finds nothing,
        and gating the entry on it left the entry ACTIVE forever with no later pass
        able to find it.
        """
        entry = self.session.exec(
            select(QueuedRun).where(QueuedRun.orchestration_run_id == orchestration_run_id)
        ).first()
        if entry and entry.status != QueuePosition.STARTED:
            entry.status = QueuePosition.STARTED
            self.session.add(entry)
            self.session.commit()

        slot = self.session.exec(
            select(AgentSlot).where(AgentSlot.current_orchestration_run_id == orchestration_run_id)
        ).first()
        if not slot:
            emit_execution_update()
            return

        slot_number = slot.slot_number
        slot.is_available = True
        slot.current_orchestration_run_id = None
        slot.current_run_id = None
        slot.released_at = datetime.now(timezone.utc)
        self.session.add(slot)
        self.session.commit()

        self.start_lane_head(slot_number)
        emit_execution_update()

    def reconcile_lanes(self) -> list[int]:
        """Put stranded entries back in line, reclaim dead lanes, then drain them.

        `on_orchestration_complete` is the happy path; it does not run when the
        release is interrupted — a crash, a restart, a failure in the tail of
        run completion. What it leaves behind is a lane that never frees and
        never starts what is queued behind it, so this runs on startup and on
        every status read rather than waiting for a hand-reset.
        """
        self._requeue_stranded_entries()
        settled = self._settle_finished_entries()
        freed = self.slots.reconcile_slots()
        # Nested child execute shares the parent's lane. Orphan-heal used to
        # bind free capacity to those children and empty the pool — undo that
        # before draining / claiming anything else.
        freed.extend(self._release_nested_slot_claims())
        # Every idle lane, not only the ones freed on this pass. A dispatch can
        # be refused — the ticket is already orchestrating, its workflow is
        # gone, the driver name is unknown — and `start_lane_head` then leaves
        # the entry queued and the lane unclaimed, deliberately, rather than
        # holding a lane for nothing. But `freed` comes from `reconcile_slots`,
        # which only selects slots that were *taken*, so a lane that was never
        # claimed appears in no list here and nothing retries it. The other
        # three callers are all events on that lane — an add, a move, a
        # completion — and a refused dispatch is none of them, so the entry sat
        # at position 1 of an idle lane until a human noticed.
        for slot_number in sorted({*freed, *self._idle_lanes_with_work()}):
            # No-op unless something is actually waiting in that lane.
            self.start_lane_head(slot_number)
        # After draining: attach free capacity to live orchestrations that
        # never claimed a slot (old bypass residue). Waiting entries go first
        # so a reclaim does not hand the lane to an orphan ahead of its queue.
        # Nested children of a slotted ancestor are not orphans — they share
        # that ancestor's lane for the life of the tree.
        claimed = self._claim_orphaned_orchestrations()
        if freed or claimed or settled:
            emit_execution_update()
        return freed

    def _ticket_covered_by_ancestor_slot(
        self, ticket_id: str, held_orchestration_ids: set[str]
    ) -> bool:
        """True when a live ancestor orchestration already holds a lane.

        BuiltinOrchestrator walks incomplete children with nested execute and
        opens a fresh OrchestrationRun per child. Those runs are intentional
        work under the parent's slot, not separate admissions.
        """
        ticket = self.session.get(Ticket, ticket_id)
        seen: set[str] = set()
        while ticket and ticket.parent_ticket_id:
            parent_id = ticket.parent_ticket_id
            if parent_id in seen:
                break
            seen.add(parent_id)
            parent_live = self.session.exec(
                select(OrchestrationRun)
                .where(OrchestrationRun.ticket_id == parent_id)
                .where(col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES))
            ).all()
            if any(run.id in held_orchestration_ids for run in parent_live):
                return True
            ticket = self.session.get(Ticket, parent_id)
        return False

    def _release_nested_slot_claims(self) -> list[int]:
        """Free slots bound to nested children when an ancestor already holds one."""
        self.slots.initialize_slots()
        slots = list(self.session.exec(select(AgentSlot)).all())
        held = {
            slot.current_orchestration_run_id for slot in slots if slot.current_orchestration_run_id
        }
        freed: list[int] = []
        for slot in slots:
            orch_id = slot.current_orchestration_run_id
            if not orch_id:
                continue
            orch = self.session.get(OrchestrationRun, orch_id)
            if not orch:
                continue
            if not self._ticket_covered_by_ancestor_slot(orch.ticket_id, held - {orch_id}):
                continue
            slot.is_available = True
            slot.current_orchestration_run_id = None
            slot.current_run_id = None
            slot.assigned_at = None
            self.session.add(slot)
            freed.append(slot.slot_number)
            logger.info(
                "Released nested slot %d claim for orchestration %s (ticket %s)",
                slot.slot_number,
                orch.id,
                orch.ticket_id,
            )
        if freed:
            self.session.commit()
        return freed

    def _claim_orphaned_orchestrations(self) -> list[int]:
        """Bind free slots to live orchestrations that never claimed one.

        Admission used to be missing from a few start paths, so agents ran while
        every lane read Available. Healing on reconcile makes the board honest
        without waiting for those runs to finish and restart through the gate.

        Nested child orchestrations under a slotted ancestor are skipped — they
        share the ancestor's lane and must not consume the rest of the pool.
        """
        self.slots.initialize_slots()
        held = {
            slot.current_orchestration_run_id
            for slot in self.session.exec(select(AgentSlot)).all()
            if slot.current_orchestration_run_id
        }
        orphans = [
            run
            for run in self.session.exec(
                select(OrchestrationRun)
                .where(col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES))
                .order_by(col(OrchestrationRun.started_at).asc())
            ).all()
            if run.id not in held and not self._ticket_covered_by_ancestor_slot(run.ticket_id, held)
        ]
        if not orphans:
            return []

        free_slots = list(
            self.session.exec(
                select(AgentSlot)
                .where(AgentSlot.is_available == True)  # noqa: E712
                .order_by(AgentSlot.slot_number)
            ).all()
        )
        claimed: list[int] = []
        for run, slot in zip(orphans, free_slots, strict=False):
            slot.is_available = False
            slot.current_orchestration_run_id = run.id
            slot.current_run_id = None
            slot.assigned_at = datetime.now(timezone.utc)
            self.session.add(slot)
            self.ensure_active_entry_for_orchestration(slot.slot_number, run.id)
            claimed.append(slot.slot_number)
            logger.info(
                "Claimed slot %d for orphaned orchestration %s (ticket %s)",
                slot.slot_number,
                run.id,
                run.ticket_id,
            )
        if claimed:
            self.session.commit()
        return claimed

    def _idle_lanes_with_work(self) -> list[int]:
        """Lanes sitting available with something already waiting in them.

        A lane in this state is wedged, not idle: `start_lane_head` refused the
        dispatch, correctly left the entry in place rather than claiming a lane
        for nothing, and returned. Nothing retries a refusal, because every
        other caller is an event on the lane — an add, a move, a completion —
        and a refusal is none of them.
        """
        self.slots.initialize_slots()
        available = self.session.exec(
            select(AgentSlot).where(AgentSlot.is_available == True)  # noqa: E712
        ).all()
        return [slot.slot_number for slot in available if self.waiting_in_lane(slot.slot_number)]

    def _requeue_stranded_entries(self) -> None:
        """Return lane entries that were started by nothing to their lane.

        An entry leaves the waiting list when a lane starts it, and the
        orchestration it started is what proves that happened. One marked
        started with no orchestration behind it was never dispatched — the
        shared-queue promoter used to take lane entries it could not run (see
        `owned_by_shared_queue`) and mark them PROMOTED, which dropped the
        ticket out of its lane while its slot stayed claimed. There is nothing
        to reconstruct: the entry goes back in line and its lane starts it.
        """
        stranded = self.session.exec(
            select(QueuedRun).where(
                col(QueuedRun.run_id).is_(None),
                col(QueuedRun.orchestration_run_id).is_(None),
                col(QueuedRun.status).in_((QueuePosition.PROMOTED, QueuePosition.ACTIVE)),
            )
        ).all()
        if not stranded:
            return

        for entry in stranded:
            logger.warning(
                "Lane entry %s (ticket %s) was marked %s without ever starting; requeueing it "
                "in lane %d",
                entry.id,
                entry.ticket_id,
                entry.status.value,
                entry.slot_number,
            )
            entry.status = QueuePosition.QUEUED
            entry.promoted_at = None
            entry.started_at = None
            self.session.add(entry)
        self.session.commit()

        for slot_number in {entry.slot_number for entry in stranded}:
            self._renumber(slot_number)

    def _entry_work_is_over(self, entry: QueuedRun) -> bool:
        """Whether the thing this entry started has reached a terminal status.

        `queued_runs` holds two regimes and this has to answer for both: a lane
        entry names an orchestration, a shared-queue or stage entry names an
        agent run. A row that has gone missing counts as over — nothing will
        ever complete it.
        """
        if entry.orchestration_run_id:
            orch = self.session.get(OrchestrationRun, entry.orchestration_run_id)
            return not orch or orch.status not in LIVE_ORCHESTRATION_STATUSES
        if entry.run_id:
            run = self.session.get(AgentRun, entry.run_id)
            return not run or run.status not in LIVE_RUN_STATUSES
        # Names nothing — `_requeue_stranded_entries` owns that shape.
        return False

    def _settle_finished_entries(self) -> list[str]:
        """Retire entries whose work finished but whose release stopped short.

        The happy paths (`on_orchestration_complete`, `on_run_complete_sync`)
        settle an entry as part of giving its lane back. Neither runs to
        completion on every route out: a restart between the two writes, or a
        success on the run side, which frees the slot but only ever writes the
        entry's status when the run *failed*.

        The residue is an entry reading ACTIVE with nothing behind it, and it is
        permanent without this — the slot is reclaimed independently by
        `reconcile_slots`, and once it no longer names the work there is nothing
        left tying the entry to what finished. `ticket_activity` counts ACTIVE
        and PROMOTED as running, so the ticket reports an agent on it forever,
        and `queue_history` counts them as live, so the lane never shows the
        blocked/failed card that would have made it visible.

        Returns the entry ids settled, so a caller can tell a quiet sweep from
        one that changed the board.
        """
        candidates = self.session.exec(
            select(QueuedRun).where(
                col(QueuedRun.status).in_((QueuePosition.ACTIVE, QueuePosition.PROMOTED))
            )
        ).all()
        settled: list[str] = []
        for entry in candidates:
            if not self._entry_work_is_over(entry):
                continue
            entry.status = QueuePosition.STARTED
            self.session.add(entry)
            settled.append(entry.id)
            logger.warning(
                "Settling lane entry %s (ticket %s, lane %d): its work finished without "
                "releasing the entry",
                entry.id,
                entry.ticket_id,
                entry.slot_number,
            )
        if settled:
            self.session.commit()
        return settled

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
