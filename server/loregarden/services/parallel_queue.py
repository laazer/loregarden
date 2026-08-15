"""Parallel execution queue service for managing concurrent agent slots.

The slot pool is **global**. A slot models this machine's capacity to run an
agent, and the machine does not have one CPU per workspace — slots used to be
keyed by workspace, so every workspace got its own pool of three and two
workspaces quietly meant six concurrent agents, each pool believing it was the
limit. Migration 0058 collapsed them.

Runs still carry a workspace: `QueuedRun.workspace_id` says which workspace the
work belongs to, and the board tags each card with it. What is no longer
per-workspace is *contention* — one pool, one waiting line, one ordering.
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
)
from loregarden.services.run_concurrency import orchestration_lease_expired
from loregarden.websocket_events import (
    QUEUE_TOPIC,
    emit_error,
    emit_execution_update,
    emit_queue_promoted,
    emit_run_completed,
)
from sqlmodel import Session, col, select, update

logger = logging.getLogger(__name__)

#: A run in one of these still has work in flight, so its slot stays claimed.
#: QUEUED counts: a run holds its slot from the moment it is assigned one, which
#: is before the executor picks it up.
LIVE_RUN_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)

#: The same, for the orchestration that holds a lane for a whole ticket.
LIVE_ORCHESTRATION_STATUSES = (
    OrchestrationRunStatus.QUEUED,
    OrchestrationRunStatus.RUNNING,
)

#: How long a slot claimed by a reservation may name nothing before a sweep
#: treats it as residue. Long enough to cover reserve-to-bind, which is a few
#: statements on the same thread; short enough that a caller which dies between
#: them costs one lane for one minute rather than until restart.
RESERVATION_GRACE = timedelta(seconds=60)

#: Statuses that mean "still waiting to start". Lives here rather than in
#: queue_lanes because the queue's own reads need it too, and one of the two
#: reading a narrower set than the other is how the board came to report a
#: queue length of zero while lanes were visibly full.
WAITING_STATUSES = (QueuePosition.QUEUED, QueuePosition.SCHEDULED)


def run_notify_fields(session: Session, run: AgentRun | None) -> dict[str, str | None]:
    """Ticket / stage / agent labels for queue toast + inbox notifications."""
    if run is None:
        return {
            "ticket_id": None,
            "ticket_title": None,
            "stage_key": None,
            "agent_id": None,
        }
    ticket_title: str | None = None
    if run.ticket_id:
        ticket = session.get(Ticket, run.ticket_id)
        ticket_title = ticket.title if ticket else None
    return {
        "ticket_id": run.ticket_id,
        "ticket_title": ticket_title,
        "stage_key": run.stage_key or None,
        "agent_id": run.agent_id or None,
    }


#: How many times a claimant re-reads the pool before giving up. A loser only
#: needs one more look — the winner has committed by then — but contention
#: between more claimants than slots can cost a couple of passes.
_CLAIM_ATTEMPTS = 4


def claim_free_slot(session: Session, *, preferred: int | None = None) -> AgentSlot | None:
    """Take a slot atomically, or return None because the pool is full.

    Every claim in this file used to be select-then-mutate: read the slots where
    `is_available`, pick one, set it False, commit. Two claimants arriving
    together both read the same row as free and both wrote it, and the second
    write simply won — one slot, two occupants, and a pool that had silently
    admitted past `max_concurrent`. The comment at the admission site asserted
    the opposite, which is the intent the code did not implement.

    The claim itself is a conditional UPDATE — `SET is_available = 0 WHERE id = ?
    AND is_available = 1` — so the database decides the winner in one statement
    and the loser sees `rowcount == 0`.

    The retry around it matters as much as the update. A candidate list is read
    once and goes stale the moment anyone else claims, and after a failed UPDATE
    this session holds a write transaction whose snapshot predates the winner's
    commit — so re-reading inside it returns the same stale list. Rolling back
    and re-reading is what lets a loser find the slot that is actually free.
    Without it a claimant could exhaust a stale list and report a full pool while
    capacity sat idle, which is how CI caught two concurrent tickets where one
    was refused against a pool of three.

    `preferred` is tried first and is a preference, not a demand: a lane that
    filled between opening a dialog and confirming should still run the ticket.
    """
    for _ in range(_CLAIM_ATTEMPTS):
        available = list(
            session.exec(
                select(AgentSlot.slot_number)
                .where(AgentSlot.is_available == True)  # noqa: E712
                .order_by(AgentSlot.slot_number)
            ).all()
        )
        if not available:
            return None

        candidates = [preferred] if preferred in available else []
        candidates.extend(number for number in available if number != preferred)

        for slot_number in candidates:
            result = session.exec(
                update(AgentSlot)
                .where(AgentSlot.slot_number == slot_number)
                .where(AgentSlot.is_available == True)  # noqa: E712
                .values(
                    is_available=False,
                    assigned_at=datetime.now(timezone.utc),
                    current_run_id=None,
                    current_orchestration_run_id=None,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                # Lost this one between the read and the write, which is exactly
                # what the conditional update exists to detect.
                continue
            session.commit()
            claimed = session.exec(
                select(AgentSlot).where(AgentSlot.slot_number == slot_number)
            ).first()
            if claimed is not None:
                # The UPDATE went round the identity map; refresh so the caller
                # does not read a stale `is_available` off a cached instance.
                session.refresh(claimed)
            return claimed

        # Every candidate was taken. Drop the stale snapshot before looking
        # again, or the next read returns the same list that just failed.
        session.rollback()

    return None


def owned_by_shared_queue():
    """Entries this service may promote: the ones naming a run it can dispatch.

    `queued_runs` holds two kinds of row. A shared-queue entry names an
    `AgentRun` that is waiting for any free slot. A *lane* entry names only a
    ticket — its run does not exist yet, and `QueueLaneService.start_lane_head`
    is what starts it, in its own lane, by dispatching a whole orchestration.

    Without this predicate the promoter took whichever came first: it claimed a
    slot for `run_id=None`, dispatched nothing (`_dispatch` refuses an empty
    id), and left the entry PROMOTED — out of its lane's waiting list forever.
    The lane then showed an occupied slot with no ticket in it, and the ticket
    never ran.
    """
    return col(QueuedRun.run_id).is_not(None)


class ParallelQueueService:
    """Manage queue of agent runs waiting for execution slots."""

    def __init__(self, session: Session, max_concurrent: int = 3):
        self.session = session
        self.max_concurrent = max_concurrent

    def _dispatch(self, run_id: str) -> None:
        """Hand a promoted run to the executor.

        Imported at call time: run_service imports orchestration, which reaches
        this module, so a module-level import here closes the cycle.
        """
        if not run_id:
            logger.warning("Refusing to dispatch an empty run id")
            return

        from loregarden.services.run_service import schedule_agent_run

        try:
            schedule_agent_run(run_id)
        except Exception:
            # A failed hand-off must not leave the slot claimed with nothing in
            # it — but it also must not take down the promotion bookkeeping that
            # already committed. Log and let the run reap as failed.
            logger.error("Failed to dispatch promoted run %s", run_id, exc_info=True)

    def _estimate_start(self, position: int) -> datetime:
        """When a run at this queue position is likely to start.

        From the median run duration across every workspace, not a flat ten
        minutes per position — that ignored both how long runs actually take
        and the fact that `max_concurrent` of them drain at once, so it
        overstated the wait by roughly the slot count. The median is drawn from
        all workspaces because they all wait in the same line now.
        """
        from loregarden.services.run_duration_stats import (
            FALLBACK_KEY,
            median_duration_by_agent,
        )

        medians = median_duration_by_agent(self.session)
        per_run = medians.get(FALLBACK_KEY)
        if not per_run:
            # No history to project from. The caller stores this as a hint, and
            # the dashboard renders a missing estimate as "—".
            return datetime.now(timezone.utc)

        waves = max(0, (position - 1) // max(1, self.max_concurrent))
        return datetime.now(timezone.utc) + timedelta(seconds=per_run * (waves + 1))

    def initialize_slots(self) -> None:
        """Top the shared pool up to `max_concurrent` slots (idempotent).

        Only ever adds. A pool that is already over capacity — which migration
        0058 can leave behind when several workspaces had runs in flight —
        drains back down through `on_run_complete` rather than by deleting a
        slot with a live agent in it.
        """
        try:
            existing_slots = self.session.exec(select(AgentSlot)).all()

            if len(existing_slots) >= self.max_concurrent:
                return

            taken = {slot.slot_number for slot in existing_slots}
            created = 0
            for slot_num in range(1, self.max_concurrent + 1):
                if slot_num in taken:
                    continue
                self.session.add(
                    AgentSlot(id=str(uuid4()), slot_number=slot_num, is_available=True)
                )
                created += 1

            self.session.commit()
            logger.info("Initialized %d shared execution slots", created)

        except Exception as e:
            logger.error(f"Error initializing slots: {e}", exc_info=True)

    async def queue_run(
        self,
        workspace_id: str,
        ticket_id: str,
        run_id: str,
        preferred_slot: int | None = None,
    ) -> dict:
        """
        Queue a run for execution or start immediately if slot available.

        Args:
            workspace_id: Workspace ID
            ticket_id: Ticket ID
            run_id: Agent run ID
            preferred_slot: Slot number the caller asked for. The queue board
                lets you stage a ticket into a specific slot, and the card
                should start in the slot it was staged in rather than jumping.
                Taken by the time we get here, we fall back to the lowest free
                slot — starting the run is what was asked for; the slot number
                is presentation.

        Returns:
            {
                "status": "queued" | "started",
                "position": 1,  # If queued
                "queue_length": 3,
                "estimated_start_at": "2026-07-06T10:30:00Z" (if queued),
                "message": "Added to queue position 3"
            }
        """
        try:
            # Initialize slots if needed
            self.initialize_slots()

            # One conditional UPDATE decides the winner. "No preference" still
            # means the lowest free slot; the ordering lives in claim_free_slot.
            available_slot = claim_free_slot(self.session, preferred=preferred_slot)

            if available_slot:
                # Start immediately. The slot is already ours; name what is in it.
                available_slot.current_run_id = run_id
                self.session.add(available_slot)
                self.session.commit()

                logger.info(
                    f"Run {run_id} started immediately on slot {available_slot.slot_number}"
                )

                emit_execution_update()

                return {
                    "status": "started",
                    "slot_number": available_slot.slot_number,
                    "message": f"Started immediately on slot {available_slot.slot_number}",
                }

            # No slots available, add to the back of the one shared queue.
            queue_length_stmt = select(QueuedRun).where(
                QueuedRun.status.in_([QueuePosition.QUEUED, QueuePosition.SCHEDULED])
            )
            queued_runs = self.session.exec(queue_length_stmt).all()
            position = len(queued_runs) + 1

            estimated_start = self._estimate_start(position)

            queued_run = QueuedRun(
                id=str(uuid4()),
                workspace_id=workspace_id,
                ticket_id=ticket_id,
                run_id=run_id,
                position=position,
                status=QueuePosition.QUEUED,
                estimated_start_at=estimated_start,
            )

            self.session.add(queued_run)
            self.session.commit()

            logger.info(f"Run {run_id} queued at position {position}")

            emit_execution_update()

            return {
                "status": "queued",
                "position": position,
                "queue_length": len(queued_runs) + 1,
                "estimated_start_at": estimated_start.isoformat(),
                "message": f"Added to queue at position {position}",
            }

        except Exception as e:
            logger.error(f"Error queueing run: {e}", exc_info=True)

            # Emit error event
            try:
                emit_error(
                    target_room=QUEUE_TOPIC,
                    message=f"Failed to queue run: {str(e)}",
                    code="QUEUE_ERROR",
                    context={"run_id": run_id},
                )
            except Exception as emit_err:
                logger.warning(f"Failed to emit error: {emit_err}")

            return {
                "status": "error",
                "message": str(e),
            }

    def reconcile_slots(self) -> list[int]:
        """Free slots whose occupant already finished. Returns the slot numbers freed.

        Releasing a slot is the last thing a completing run does, and everything
        before it — artifact refresh, log finalisation, the ticket load — can
        fail or be interrupted by a restart. The residue is a slot pinned to a
        run that reached a terminal status hours ago: capacity nothing can
        reclaim, and a board that reports the lane as running whatever that run
        last was, which is how slot 1 came to read "succeeded".

        The same shape as `settle_stranded_stages` in `run_service`, for the
        same reason: a terminal run will never be selected by a reap again, so
        without a sweep over what it left behind the state is permanent.

        Occupancy is whichever id the slot holds — an orchestration for a lane,
        an agent run for a single-stage run. A slot holding neither, or holding
        an id whose row is gone, is not occupied either.
        """
        freed: list[int] = []
        try:
            occupied = self.session.exec(
                select(AgentSlot).where(AgentSlot.is_available == False)
            ).all()
            for slot in occupied:
                if self._occupant_is_live(slot):
                    continue
                logger.info(
                    "Reclaiming slot %d: its occupant (%s) is no longer running",
                    slot.slot_number,
                    slot.current_orchestration_run_id or slot.current_run_id or "nothing",
                )
                slot.is_available = True
                slot.current_run_id = None
                slot.current_orchestration_run_id = None
                slot.released_at = datetime.now(timezone.utc)
                self.session.add(slot)
                freed.append(slot.slot_number)

            if freed:
                self.session.commit()
        except Exception:
            # Best-effort: a sweep that cannot run must not take the board's
            # status read down with it.
            self.session.rollback()
            logger.warning("Failed to reconcile execution slots", exc_info=True)
            return []

        return freed

    def _occupant_is_live(self, slot: AgentSlot) -> bool:
        """Whether whatever holds this slot still has work in flight.

        A slot naming nothing is usually dead residue, but for a moment it is a
        *fresh reservation*: `QueueAdmissionService` claims a slot with both ids
        null so two requests cannot read it as free, and the caller fills them in
        on `bind`. The two are indistinguishable by id alone, and a status read
        landing in that window — the queue websocket polls every few seconds —
        would reclaim a slot that is about to be bound and let a second
        orchestration into it. A reservation that never binds is still collected,
        just a grace period later.
        """
        if slot.current_orchestration_run_id:
            orch_run = self.session.get(OrchestrationRun, slot.current_orchestration_run_id)
            if not orch_run or orch_run.status not in LIVE_ORCHESTRATION_STATUSES:
                return False
            # Status alone is a promise only the run's own owner can keep. An
            # external harness that walked away left RUNNING behind and held its
            # lane permanently — not until restart, permanently. The lease is
            # renewed by the work itself, so this asks whether anything has
            # happened rather than whether anyone remembered to say goodbye.
            return not orchestration_lease_expired(orch_run)
        if slot.current_run_id:
            run = self.session.get(AgentRun, slot.current_run_id)
            return bool(run) and run.status in LIVE_RUN_STATUSES
        return self._within_reservation_grace(slot)

    def _within_reservation_grace(self, slot: AgentSlot) -> bool:
        """Whether a slot naming nothing was claimed too recently to reclaim."""
        assigned_at = slot.assigned_at
        if assigned_at is None:
            return False
        if assigned_at.tzinfo is None:
            assigned_at = assigned_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - assigned_at < RESERVATION_GRACE

    async def get_active_runs(self) -> list[dict]:
        """
        Get what is executing in each occupied slot, across all workspaces.

        A slot is held by an orchestration (a lane running a whole ticket) or by
        a single agent run, and both have to be reported: keying this on
        `current_run_id` alone left every lane-started ticket invisible, so the
        board drew an occupied lane as "Available" and offered to start another
        ticket in it.

        Returns:
            List of {
                "run_id": "...",
                "slot_number": 1,
                "ticket_id": "...",
                "workspace_id": "...",
                "assigned_at": "2026-07-06T10:00:00Z",
                "elapsed_seconds": 300
            }
        """
        try:
            slot_stmt = select(AgentSlot).where(AgentSlot.is_available == False)
            active_slots = self.session.exec(slot_stmt).all()

            active_runs = []
            now = datetime.now(timezone.utc)

            for slot in active_slots:
                card = self._occupant_card(slot)
                if not card:
                    continue

                assigned_at = slot.assigned_at
                if assigned_at and assigned_at.tzinfo is None:
                    assigned_at = assigned_at.replace(tzinfo=timezone.utc)
                elapsed = (now - assigned_at).total_seconds() if assigned_at else 0
                card.update(
                    {
                        "slot_number": slot.slot_number,
                        "assigned_at": slot.assigned_at.isoformat() if slot.assigned_at else None,
                        # The slot's own clock, not the current run's: a lane has
                        # been busy since it was claimed, across every stage.
                        "elapsed_seconds": int(elapsed),
                    }
                )
                active_runs.append(card)

            return active_runs

        except Exception as e:
            logger.error(f"Error getting active runs: {e}", exc_info=True)
            return []

    def _occupant_card(self, slot: AgentSlot) -> dict | None:
        """What to show for an occupied slot, or None if nothing holds it."""
        if slot.current_orchestration_run_id:
            return self._orchestration_card(slot.current_orchestration_run_id)
        if slot.current_run_id:
            run = self.session.get(AgentRun, slot.current_run_id)
            return self._run_card(run) if run else None
        return None

    def _orchestration_card(self, orchestration_run_id: str) -> dict | None:
        """A lane's card: the ticket it is running, described by its live stage.

        The status is the *lane's*, not the stage's. A ticket's pipeline spans
        many agent runs and the lane keeps holding the slot between them, so
        reporting whichever run finished last would have the card read
        "succeeded" while the ticket is still going.
        """
        orch_run = self.session.get(OrchestrationRun, orchestration_run_id)
        if not orch_run:
            return None

        run = self.session.exec(
            select(AgentRun)
            .where(AgentRun.orchestration_run_id == orchestration_run_id)
            .order_by(col(AgentRun.started_at).desc())
        ).first()
        return {
            "run_id": run.id if run else "",
            "orchestration_run_id": orch_run.id,
            "ticket_id": orch_run.ticket_id,
            # The pool is shared, so which workspace a card belongs to has to
            # travel with the card.
            "workspace_id": orch_run.workspace_id,
            "agent_id": run.agent_id if run else "",
            "stage_key": run.stage_key if run else orch_run.current_stage_key,
            "status": orch_run.status.value,
        }

    def _run_card(self, run: AgentRun) -> dict:
        """A single-stage run's card."""
        return {
            "run_id": run.id,
            "orchestration_run_id": run.orchestration_run_id or "",
            "ticket_id": run.ticket_id,
            "workspace_id": run.workspace_id,
            "agent_id": run.agent_id,
            "stage_key": run.stage_key,
            "status": run.status.value,
        }

    async def get_queued_runs(self) -> list[dict]:
        """
        Get everything waiting to start, across all workspaces and lanes.

        A lane entry has no ``run_id`` — nothing runs on a ticket's behalf
        until its lane reaches it — so keying this on the agent run behind the
        entry dropped every one of them, and with it the queue length and the
        clear-time projection the board is built from. The entry itself is the
        record; the agent run is consulted only when one already exists.

        Returns:
            List of {
                "entry_id": "...",
                "run_id": "...",
                "ticket_id": "...",
                "slot_number": 1,
                "position": 2,
                "entry_kind": "orchestration",
                "estimated_start_at": "2026-07-06T10:10:00Z",
                "queued_at": "2026-07-06T10:00:00Z",
                "wait_seconds": 180
            }
        """
        try:
            queue_stmt = (
                select(QueuedRun)
                .where(QueuedRun.status.in_(list(WAITING_STATUSES)))
                .order_by(QueuedRun.slot_number, QueuedRun.position)
            )

            queued_runs_records = list(self.session.exec(queue_stmt).all())
            if not queued_runs_records:
                return []

            run_ids = [qr.run_id for qr in queued_runs_records if qr.run_id]
            runs: dict[str, AgentRun] = {}
            if run_ids:
                rows = self.session.exec(
                    select(AgentRun).where(col(AgentRun.id).in_(run_ids))
                ).all()
                runs = {run.id: run for run in rows}

            tickets: dict[str, Ticket] = {}
            ticket_ids = [qr.ticket_id for qr in queued_runs_records if qr.ticket_id]
            if ticket_ids:
                rows = self.session.exec(select(Ticket).where(col(Ticket.id).in_(ticket_ids))).all()
                tickets = {ticket.id: ticket for ticket in rows}

            queued_runs = []
            now = datetime.now(timezone.utc)

            for qr in queued_runs_records:
                run = runs.get(qr.run_id or "")
                ticket = tickets.get(qr.ticket_id or "")
                created_at = qr.created_at
                if created_at and created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                wait = (now - created_at).total_seconds() if created_at else 0

                # An entry that has not started has no agent of its own. The
                # ticket's next agent is the one it will dispatch, which is
                # what an estimate for this entry has to be priced against.
                agent_id = run.agent_id if run else (ticket.next_agent if ticket else "")
                stage_key = (
                    run.stage_key
                    if run
                    else (qr.stage_key or (ticket.workflow_stage_key if ticket else ""))
                )

                queued_runs.append(
                    {
                        "entry_id": qr.id,
                        "run_id": qr.run_id or "",
                        "ticket_id": qr.ticket_id,
                        "workspace_id": qr.workspace_id,
                        "agent_id": agent_id,
                        "stage_key": stage_key,
                        "slot_number": qr.slot_number,
                        "position": qr.position,
                        # "orchestration" costs a whole pipeline, "stage" costs
                        # one run — an estimate that ignores the difference is
                        # wrong by the length of a workflow.
                        "entry_kind": qr.entry_kind,
                        # The lane card shows these, and they are answers from
                        # a dialog that closed long before this entry starts.
                        "auto_approve": qr.auto_approve,
                        "stop_at_stage_key": qr.stop_at_stage_key,
                        "estimated_start_at": qr.estimated_start_at.isoformat()
                        if qr.estimated_start_at
                        else None,
                        "queued_at": created_at.isoformat() if created_at else None,
                        "wait_seconds": int(wait),
                    }
                )

            return queued_runs

        except Exception as e:
            logger.error(f"Error getting queued runs: {e}", exc_info=True)
            return []

    async def promote_from_queue(self, run_id: str | None = None) -> dict | None:
        """Async wrapper kept for the API layer; the work is sync."""
        return self.promote_from_queue_sync(run_id)

    def promote_from_queue_sync(self, run_id: str | None = None) -> dict | None:
        """
        Check if queue has items and slots available.
        If yes, promote next queued run to active slot.

        The queue is shared, so the head of the line is the head across every
        workspace — a freed slot goes to whoever has waited longest, not to
        whoever freed it.

        Args:
            run_id: Promote this specific queued run instead of the head of the
                queue. Used by the manual-promote endpoint; when None the
                longest-waiting run is promoted.

        Returns:
            {
                "run_id": "...",
                "ticket_id": "...",
                "slot_number": 1,
                "message": "Promoted from queue position 1"
            } or None if no promotion needed
        """
        try:
            # Read what there is to promote before taking a slot for it. The
            # claim is atomic and therefore committed, so claiming first and
            # finding nothing to run would strand a lane nobody is using.
            conditions = [QueuedRun.status == QueuePosition.QUEUED, owned_by_shared_queue()]
            if run_id is not None:
                conditions.append(QueuedRun.run_id == run_id)

            queue_stmt = select(QueuedRun).where(*conditions).order_by(QueuedRun.position)

            queued_run = self.session.exec(queue_stmt).first()

            if not queued_run:
                logger.info("No queued runs to promote")
                return None

            # Claimed atomically: a promotion racing an admission both read the
            # same free slot and both wrote it.
            available_slot = claim_free_slot(self.session)

            if not available_slot:
                logger.info("No available execution slots")
                return None

            # The slot is already ours; name what is in it.
            available_slot.current_run_id = queued_run.run_id

            queued_run.status = QueuePosition.PROMOTED
            queued_run.promoted_at = datetime.now(timezone.utc)
            queued_run.started_at = datetime.now(timezone.utc)

            self.session.add(available_slot)
            self.session.add(queued_run)
            self.session.commit()

            logger.info(
                f"Promoted run {queued_run.run_id} from position {queued_run.position} "
                f"to slot {available_slot.slot_number}"
            )

            # Actually start it. Promotion used to move DB rows and stop there,
            # so a run that waited for a slot got the slot and then never ran —
            # the queue only ever drained by hand.
            self._dispatch(queued_run.run_id)

            # Emit queue promoted event
            try:
                promoted_run = self.session.get(AgentRun, queued_run.run_id)
                emit_queue_promoted(
                    run_id=queued_run.run_id,
                    slot_number=available_slot.slot_number,
                    **run_notify_fields(self.session, promoted_run),
                )
            except Exception as e:
                logger.warning(f"Failed to emit queue_promoted: {e}")

            # Re-order remaining queue
            self._reorder_queue_sync()

            emit_execution_update()

            return {
                "run_id": queued_run.run_id,
                "ticket_id": queued_run.ticket_id,
                "slot_number": available_slot.slot_number,
                "message": f"Promoted from position {queued_run.position} to slot {available_slot.slot_number}",
            }

        except Exception as e:
            logger.error(f"Error promoting from queue: {e}", exc_info=True)

            # Emit error event
            try:
                emit_error(
                    target_room=QUEUE_TOPIC,
                    message=f"Failed to promote from queue: {str(e)}",
                    code="PROMOTION_ERROR",
                    context={},
                )
            except Exception as emit_err:
                logger.warning(f"Failed to emit error: {emit_err}")

            return None

    async def on_run_complete(self, run_id: str) -> dict | None:
        """Async wrapper kept for existing callers; the work is sync."""
        return self.on_run_complete_sync(run_id)

    def on_run_complete_sync(self, run_id: str) -> dict | None:
        """
        Called when an agent run completes.
        Frees up the slot and promotes next from queue.

        Sync on purpose. Every run reaches its terminal status through
        `complete_run_tail`, which is sync, and that is the only place a slot
        release can be hooked so that it happens however the run was started.
        Nothing here awaits — the session is sync — so the async twin above is
        a signature, not a behaviour.

        Args:
            run_id: Completed agent run ID

        Returns:
            Next run promoted, or None if queue empty
        """
        try:
            # Get run details for event emission
            run = self.session.get(AgentRun, run_id)
            run_status = run.status.value if run else "completed"

            # Emit run completed event
            try:
                emit_run_completed(
                    run_id=run_id,
                    status=run_status,
                    **run_notify_fields(self.session, run),
                )
            except Exception as e:
                logger.warning(f"Failed to emit run_completed: {e}")

            # Mark the queue entry FAILED so retry-all-failed / get-failed-runs
            # (api/bulk_queue_operations.py) can actually see and act on it —
            # previously nothing ever set QueuedRun.status to FAILED.
            if run and run.status == RunStatus.FAILED:
                queued_stmt = select(QueuedRun).where(QueuedRun.run_id == run_id)
                queued_run = self.session.exec(queued_stmt).first()
                if queued_run:
                    queued_run.status = QueuePosition.FAILED
                    queued_run.failure_reason = (run.stderr or "Agent run failed")[:2000]
                    queued_run.last_failed_at = datetime.now(timezone.utc)
                    self.session.add(queued_run)
                    self.session.commit()

            # Find slot with this run
            slot_stmt = select(AgentSlot).where(AgentSlot.current_run_id == run_id)
            slot = self.session.exec(slot_stmt).first()

            if slot:
                total_slots = len(self.session.exec(select(AgentSlot)).all())
                if total_slots > self.max_concurrent:
                    # The pool is over capacity — migration 0058 keeps every
                    # occupied slot when it collapses the per-workspace pools,
                    # which can exceed `max_concurrent`. Reclaim the surplus as
                    # it frees rather than refilling it, so the pool converges
                    # on the real limit instead of staying permanently wide.
                    self.session.delete(slot)
                    logger.info(
                        "Reclaimed surplus slot %d on release of run %s",
                        slot.slot_number,
                        run_id,
                    )
                else:
                    slot.is_available = True
                    slot.current_run_id = None
                    slot.released_at = datetime.now(timezone.utc)
                    self.session.add(slot)
                    logger.info(f"Released run {run_id} from slot {slot.slot_number}")
                self.session.commit()

            # Promote from queue (promote_from_queue already emits events)
            promoted = self.promote_from_queue_sync()

            emit_execution_update()

            if promoted:
                return {
                    "status": "promoted",
                    "next_run": promoted,
                }
            else:
                return {
                    "status": "slot_freed",
                    "message": "Slot freed, no runs in queue",
                }

        except Exception as e:
            logger.error(f"Error on run complete: {e}", exc_info=True)

            # Emit error event
            try:
                emit_error(
                    target_room=QUEUE_TOPIC,
                    message=f"Failed to handle run completion: {str(e)}",
                    code="COMPLETION_HANDLER_ERROR",
                    context={"run_id": run_id},
                )
            except Exception as emit_err:
                logger.warning(f"Failed to emit error: {emit_err}")

            return None

    async def _reorder_queue(self) -> None:
        self._reorder_queue_sync()

    def _reorder_queue_sync(self) -> None:
        """Re-close the gaps in the one shared waiting line after a promotion.

        Only the shared line. A lane entry's position is its place in *its
        lane*, so renumbering every waiting row into one sequence reordered
        lanes that had nothing to do with this promotion.
        """
        try:
            queue_stmt = (
                select(QueuedRun)
                .where(QueuedRun.status == QueuePosition.QUEUED, owned_by_shared_queue())
                .order_by(QueuedRun.position)
            )

            queued_runs = self.session.exec(queue_stmt).all()

            for idx, qr in enumerate(queued_runs, 1):
                qr.position = idx
                # Re-estimate start time
                qr.estimated_start_at = datetime.now(timezone.utc) + timedelta(minutes=10 * idx)
                self.session.add(qr)

            self.session.commit()

        except Exception as e:
            logger.error(f"Error reordering queue: {e}", exc_info=True)

    async def cancel_queued_run(self, run_id: str) -> bool:
        """
        Cancel a queued run (remove from queue).

        Args:
            run_id: Agent run ID to cancel

        Returns:
            True if cancelled, False if not found or error
        """
        try:
            queue_stmt = select(QueuedRun).where(QueuedRun.run_id == run_id)
            queued_run = self.session.exec(queue_stmt).first()

            if not queued_run:
                logger.warning(f"Queued run not found: {run_id}")
                return False

            self.session.delete(queued_run)
            self.session.commit()

            logger.info(f"Cancelled queued run {run_id}")

            # Re-order remaining queue
            self._reorder_queue_sync()

            return True

        except Exception as e:
            logger.error(f"Error cancelling queued run: {e}", exc_info=True)
            return False

    def get_queue_stats(self) -> dict:
        """
        Get statistics for the shared queue.

        Returns:
            {
                "max_concurrent": 3,
                "active_count": 2,
                "available_slots": 1,
                "queued_count": 5,
                "total_slots_occupied": 2,
                "longest_wait_seconds": 900
            }
        """
        try:
            # Count active slots
            active_stmt = select(AgentSlot).where(AgentSlot.is_available == False)
            active_slots = self.session.exec(active_stmt).all()
            active_count = len(active_slots)

            # Everything still waiting, in either regime. Counting only QUEUED
            # left every SCHEDULED lane entry out of the length the board draws
            # its "Queued" figure from.
            queue_stmt = select(QueuedRun).where(QueuedRun.status.in_(list(WAITING_STATUSES)))
            queued_runs = self.session.exec(queue_stmt).all()
            queued_count = len(queued_runs)

            # How long the oldest entry has *already* waited. Backward-looking
            # and measured, unlike the projected wait, which is derived from
            # run history in run_duration_stats — the board shows both and must
            # not confuse them.
            longest_wait_seconds = 0
            if queued_runs:
                oldest_queue = min(queued_runs, key=lambda r: r.created_at)
                oldest_created_at = oldest_queue.created_at
                if oldest_created_at and oldest_created_at.tzinfo is None:
                    oldest_created_at = oldest_created_at.replace(tzinfo=timezone.utc)
                oldest_age = (datetime.now(timezone.utc) - oldest_created_at).total_seconds()
                longest_wait_seconds = int(max(0.0, oldest_age))

            # A pool left over capacity by migration 0058 still has every one of
            # those runs executing. Reporting the nominal limit would draw fewer
            # lanes than there are running agents and hide the overflow, so the
            # reported width is whichever is larger; it converges back down as
            # the surplus slots are reclaimed on release.
            effective_slots = max(self.max_concurrent, active_count)

            return {
                "max_concurrent": effective_slots,
                "active_count": active_count,
                "available_slots": effective_slots - active_count,
                "queued_count": queued_count,
                "total_slots_occupied": active_count,
                # Seconds, because a lane entry that has waited 40 seconds is
                # not "0m" — which is what the board showed for anything under
                # a minute, on the only stat that was supposed to prove the
                # queue was moving.
                "longest_wait_seconds": longest_wait_seconds,
            }

        except Exception as e:
            logger.error(f"Error getting queue stats: {e}", exc_info=True)
            return {}
