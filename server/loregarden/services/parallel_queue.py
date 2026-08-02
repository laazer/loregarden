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
    QueuedRun,
    QueuePosition,
    RunStatus,
)
from loregarden.websocket_events import (
    QUEUE_TOPIC,
    emit_error,
    emit_execution_update,
    emit_queue_promoted,
    emit_run_completed,
)
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


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

            # Check available slots. Ordered so that "no preference" means the
            # lowest free slot every time, rather than whatever the row order
            # happened to be.
            slot_stmt = (
                select(AgentSlot)
                .where(AgentSlot.is_available == True)
                .order_by(AgentSlot.slot_number)
            )
            available_slots = self.session.exec(slot_stmt).all()

            available_slot = None
            if preferred_slot is not None:
                available_slot = next(
                    (slot for slot in available_slots if slot.slot_number == preferred_slot),
                    None,
                )
            if available_slot is None:
                available_slot = available_slots[0] if available_slots else None

            if available_slot:
                # Start immediately
                available_slot.is_available = False
                available_slot.current_run_id = run_id
                available_slot.assigned_at = datetime.now(timezone.utc)
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

    async def get_active_runs(self) -> list[dict]:
        """
        Get currently executing runs (occupying slots), across all workspaces.

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
                if not slot.current_run_id:
                    continue

                # Get run details
                run_stmt = select(AgentRun).where(AgentRun.id == slot.current_run_id)
                run = self.session.exec(run_stmt).first()

                if run:
                    assigned_at = slot.assigned_at
                    if assigned_at and assigned_at.tzinfo is None:
                        assigned_at = assigned_at.replace(tzinfo=timezone.utc)
                    elapsed = (now - assigned_at).total_seconds() if assigned_at else 0
                    active_runs.append(
                        {
                            "run_id": run.id,
                            "slot_number": slot.slot_number,
                            "ticket_id": run.ticket_id,
                            # The pool is shared, so which workspace a card
                            # belongs to has to travel with the card.
                            "workspace_id": run.workspace_id,
                            "agent_id": run.agent_id,
                            "stage_key": run.stage_key,
                            "assigned_at": slot.assigned_at.isoformat()
                            if slot.assigned_at
                            else None,
                            "elapsed_seconds": int(elapsed),
                            "status": run.status.value,
                        }
                    )

            return active_runs

        except Exception as e:
            logger.error(f"Error getting active runs: {e}", exc_info=True)
            return []

    async def get_queued_runs(self) -> list[dict]:
        """
        Get runs waiting in the shared queue, across all workspaces.

        Returns:
            List of {
                "run_id": "...",
                "ticket_id": "...",
                "position": 2,
                "estimated_start_at": "2026-07-06T10:10:00Z",
                "queued_at": "2026-07-06T10:00:00Z",
                "wait_seconds": 180
            }
        """
        try:
            queue_stmt = (
                select(QueuedRun)
                .where(QueuedRun.status.in_([QueuePosition.QUEUED, QueuePosition.SCHEDULED]))
                .order_by(QueuedRun.position)
            )

            queued_runs_records = self.session.exec(queue_stmt).all()
            queued_runs = []
            now = datetime.now(timezone.utc)

            for qr in queued_runs_records:
                # Get run details
                run_stmt = select(AgentRun).where(AgentRun.id == qr.run_id)
                run = self.session.exec(run_stmt).first()

                if run:
                    created_at = qr.created_at
                    if created_at and created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    wait = (now - created_at).total_seconds() if created_at else 0
                    queued_runs.append(
                        {
                            "run_id": run.id,
                            "ticket_id": run.ticket_id,
                            "workspace_id": run.workspace_id,
                            "agent_id": run.agent_id,
                            "stage_key": run.stage_key,
                            "position": qr.position,
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
            # Find available slot
            slot_stmt = (
                select(AgentSlot)
                .where(AgentSlot.is_available == True)
                .order_by(AgentSlot.slot_number)
            )
            available_slot = self.session.exec(slot_stmt).first()

            if not available_slot:
                logger.info("No available execution slots")
                return None

            # Find the requested queued run, or the first one if unspecified
            conditions = [QueuedRun.status == QueuePosition.QUEUED]
            if run_id is not None:
                conditions.append(QueuedRun.run_id == run_id)

            queue_stmt = select(QueuedRun).where(*conditions).order_by(QueuedRun.position)

            queued_run = self.session.exec(queue_stmt).first()

            if not queued_run:
                logger.info("No queued runs to promote")
                return None

            # Promote run to slot
            available_slot.is_available = False
            available_slot.current_run_id = queued_run.run_id
            available_slot.assigned_at = datetime.now(timezone.utc)

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
                emit_queue_promoted(
                    run_id=queued_run.run_id,
                    slot_number=available_slot.slot_number,
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
                emit_run_completed(run_id=run_id, status=run_status)
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
        """Re-close the gaps in the one shared waiting line after a promotion."""
        try:
            queue_stmt = (
                select(QueuedRun)
                .where(QueuedRun.status == QueuePosition.QUEUED)
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
                "queue_wait_time_minutes": 15
            }
        """
        try:
            # Count active slots
            active_stmt = select(AgentSlot).where(AgentSlot.is_available == False)
            active_slots = self.session.exec(active_stmt).all()
            active_count = len(active_slots)

            # Count queued runs
            queue_stmt = select(QueuedRun).where(QueuedRun.status == QueuePosition.QUEUED)
            queued_runs = self.session.exec(queue_stmt).all()
            queued_count = len(queued_runs)

            # Calculate estimated queue wait time
            queue_wait_minutes = 0
            if queued_runs:
                oldest_queue = min(queued_runs, key=lambda r: r.created_at)
                oldest_created_at = oldest_queue.created_at
                if oldest_created_at and oldest_created_at.tzinfo is None:
                    oldest_created_at = oldest_created_at.replace(tzinfo=timezone.utc)
                oldest_age = (datetime.now(timezone.utc) - oldest_created_at).total_seconds()
                queue_wait_minutes = int(oldest_age / 60)

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
                "queue_wait_time_minutes": queue_wait_minutes,
            }

        except Exception as e:
            logger.error(f"Error getting queue stats: {e}", exc_info=True)
            return {}
