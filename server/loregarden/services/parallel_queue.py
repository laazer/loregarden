"""Parallel execution queue service for managing concurrent agent slots."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlmodel import Session, select

from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    QueuedRun,
    QueuePosition,
    RunStatus,
)
from loregarden.websocket_events import (
    emit_execution_update,
    emit_queue_promoted,
    emit_run_completed,
    emit_error,
)

logger = logging.getLogger(__name__)


class ParallelQueueService:
    """Manage queue of agent runs waiting for execution slots."""

    def __init__(self, session: Session, max_concurrent: int = 3):
        self.session = session
        self.max_concurrent = max_concurrent

    def initialize_slots(self, workspace_id: str) -> None:
        """
        Initialize execution slots for a workspace (one-time setup).

        Args:
            workspace_id: Workspace ID
        """
        try:
            # Check if slots already exist
            stmt = select(AgentSlot).where(AgentSlot.workspace_id == workspace_id)
            existing_slots = self.session.exec(stmt).all()

            if len(existing_slots) >= self.max_concurrent:
                logger.info(f"Slots already initialized for workspace {workspace_id}")
                return

            # Create slots
            for slot_num in range(1, self.max_concurrent + 1):
                slot = AgentSlot(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    slot_number=slot_num,
                    is_available=True,
                )
                self.session.add(slot)

            self.session.commit()
            logger.info(f"Initialized {self.max_concurrent} slots for workspace {workspace_id}")

        except Exception as e:
            logger.error(f"Error initializing slots: {e}", exc_info=True)

    async def queue_run(
        self,
        workspace_id: str,
        ticket_id: str,
        run_id: str,
    ) -> dict:
        """
        Queue a run for execution or start immediately if slot available.

        Args:
            workspace_id: Workspace ID
            ticket_id: Ticket ID
            run_id: Agent run ID

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
            self.initialize_slots(workspace_id)

            # Check available slots
            slot_stmt = select(AgentSlot).where(
                (AgentSlot.workspace_id == workspace_id)
                & (AgentSlot.is_available == True)
            )
            available_slot = self.session.exec(slot_stmt).first()

            if available_slot:
                # Start immediately
                available_slot.is_available = False
                available_slot.current_run_id = run_id
                available_slot.assigned_at = datetime.now(timezone.utc)
                self.session.add(available_slot)
                self.session.commit()

                logger.info(f"Run {run_id} started immediately on slot {available_slot.slot_number}")

                # Emit execution update
                try:
                    active_runs = await self.get_active_runs(workspace_id)
                    queued_runs = await self.get_queued_runs(workspace_id)
                    stats = self.get_queue_stats(workspace_id)

                    emit_execution_update(
                        workspace_id=workspace_id,
                        active_runs=active_runs,
                        queued_runs=queued_runs,
                        stats=stats,
                    )
                except Exception as e:
                    logger.warning(f'Failed to emit execution_update: {e}')

                return {
                    "status": "started",
                    "slot_number": available_slot.slot_number,
                    "message": f"Started immediately on slot {available_slot.slot_number}",
                }

            # No slots available, add to queue
            queue_length_stmt = select(QueuedRun).where(
                (QueuedRun.workspace_id == workspace_id)
                & (QueuedRun.status.in_([QueuePosition.QUEUED, QueuePosition.SCHEDULED]))
            )
            queued_runs = self.session.exec(queue_length_stmt).all()
            position = len(queued_runs) + 1

            # Estimate start time (assuming 10 min per run)
            estimated_start = datetime.now(timezone.utc) + timedelta(minutes=10 * position)

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

            # Emit execution update
            try:
                active_runs = await self.get_active_runs(workspace_id)
                queued_runs = await self.get_queued_runs(workspace_id)
                stats = self.get_queue_stats(workspace_id)

                emit_execution_update(
                    workspace_id=workspace_id,
                    active_runs=active_runs,
                    queued_runs=queued_runs,
                    stats=stats,
                )
            except Exception as e:
                logger.warning(f'Failed to emit execution_update: {e}')

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
                    target_room=f'workspace:{workspace_id}',
                    message=f'Failed to queue run: {str(e)}',
                    code='QUEUE_ERROR',
                    context={'run_id': run_id}
                )
            except Exception as emit_err:
                logger.warning(f'Failed to emit error: {emit_err}')

            return {
                "status": "error",
                "message": str(e),
            }

    async def get_active_runs(self, workspace_id: str) -> list[dict]:
        """
        Get currently executing runs (occupying slots).

        Args:
            workspace_id: Workspace ID

        Returns:
            List of {
                "run_id": "...",
                "slot_number": 1,
                "ticket_id": "...",
                "assigned_at": "2026-07-06T10:00:00Z",
                "elapsed_seconds": 300
            }
        """
        try:
            slot_stmt = select(AgentSlot).where(
                (AgentSlot.workspace_id == workspace_id)
                & (AgentSlot.is_available == False)
            )
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
                    elapsed = (now - slot.assigned_at).total_seconds() if slot.assigned_at else 0
                    active_runs.append({
                        "run_id": run.id,
                        "slot_number": slot.slot_number,
                        "ticket_id": run.ticket_id,
                        "agent_id": run.agent_id,
                        "assigned_at": slot.assigned_at.isoformat() if slot.assigned_at else None,
                        "elapsed_seconds": int(elapsed),
                        "status": run.status.value,
                    })

            return active_runs

        except Exception as e:
            logger.error(f"Error getting active runs: {e}", exc_info=True)
            return []

    async def get_queued_runs(self, workspace_id: str) -> list[dict]:
        """
        Get runs waiting in queue.

        Args:
            workspace_id: Workspace ID

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
            queue_stmt = select(QueuedRun).where(
                (QueuedRun.workspace_id == workspace_id)
                & (QueuedRun.status.in_([QueuePosition.QUEUED, QueuePosition.SCHEDULED]))
            ).order_by(QueuedRun.position)

            queued_runs_records = self.session.exec(queue_stmt).all()
            queued_runs = []
            now = datetime.now(timezone.utc)

            for qr in queued_runs_records:
                # Get run details
                run_stmt = select(AgentRun).where(AgentRun.id == qr.run_id)
                run = self.session.exec(run_stmt).first()

                if run:
                    wait = (now - qr.created_at).total_seconds() if qr.created_at else 0
                    queued_runs.append({
                        "run_id": run.id,
                        "ticket_id": run.ticket_id,
                        "agent_id": run.agent_id,
                        "position": qr.position,
                        "estimated_start_at": qr.estimated_start_at.isoformat() if qr.estimated_start_at else None,
                        "queued_at": qr.created_at.isoformat() if qr.created_at else None,
                        "wait_seconds": int(wait),
                    })

            return queued_runs

        except Exception as e:
            logger.error(f"Error getting queued runs: {e}", exc_info=True)
            return []

    async def promote_from_queue(self, workspace_id: str) -> Optional[dict]:
        """
        Check if queue has items and slots available.
        If yes, promote next queued run to active slot.

        Args:
            workspace_id: Workspace ID

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
            slot_stmt = select(AgentSlot).where(
                (AgentSlot.workspace_id == workspace_id)
                & (AgentSlot.is_available == True)
            )
            available_slot = self.session.exec(slot_stmt).first()

            if not available_slot:
                logger.info(f"No available slots in workspace {workspace_id}")
                return None

            # Find first queued run
            queue_stmt = select(QueuedRun).where(
                (QueuedRun.workspace_id == workspace_id)
                & (QueuedRun.status == QueuePosition.QUEUED)
            ).order_by(QueuedRun.position)

            queued_run = self.session.exec(queue_stmt).first()

            if not queued_run:
                logger.info(f"No queued runs in workspace {workspace_id}")
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

            # Emit queue promoted event
            try:
                emit_queue_promoted(
                    workspace_id=workspace_id,
                    run_id=queued_run.run_id,
                    slot_number=available_slot.slot_number,
                )
            except Exception as e:
                logger.warning(f'Failed to emit queue_promoted: {e}')

            # Re-order remaining queue
            await self._reorder_queue(workspace_id)

            # Emit execution update with new state
            try:
                active_runs = await self.get_active_runs(workspace_id)
                queued_runs = await self.get_queued_runs(workspace_id)
                stats = self.get_queue_stats(workspace_id)

                emit_execution_update(
                    workspace_id=workspace_id,
                    active_runs=active_runs,
                    queued_runs=queued_runs,
                    stats=stats,
                )
            except Exception as e:
                logger.warning(f'Failed to emit execution_update: {e}')

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
                    target_room=f'workspace:{workspace_id}',
                    message=f'Failed to promote from queue: {str(e)}',
                    code='PROMOTION_ERROR',
                    context={'workspace_id': workspace_id}
                )
            except Exception as emit_err:
                logger.warning(f'Failed to emit error: {emit_err}')

            return None

    async def on_run_complete(self, workspace_id: str, run_id: str) -> Optional[dict]:
        """
        Called when an agent run completes.
        Frees up the slot and promotes next from queue.

        Args:
            workspace_id: Workspace ID
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
                    workspace_id=workspace_id,
                    run_id=run_id,
                    status=run_status,
                )
            except Exception as e:
                logger.warning(f'Failed to emit run_completed: {e}')

            # Find slot with this run
            slot_stmt = select(AgentSlot).where(AgentSlot.current_run_id == run_id)
            slot = self.session.exec(slot_stmt).first()

            if slot:
                slot.is_available = True
                slot.current_run_id = None
                slot.released_at = datetime.now(timezone.utc)
                self.session.add(slot)
                self.session.commit()

                logger.info(f"Released run {run_id} from slot {slot.slot_number}")

            # Promote from queue (promote_from_queue already emits events)
            promoted = await self.promote_from_queue(workspace_id)

            # Emit final execution update
            try:
                active_runs = await self.get_active_runs(workspace_id)
                queued_runs = await self.get_queued_runs(workspace_id)
                stats = self.get_queue_stats(workspace_id)

                emit_execution_update(
                    workspace_id=workspace_id,
                    active_runs=active_runs,
                    queued_runs=queued_runs,
                    stats=stats,
                )
            except Exception as e:
                logger.warning(f'Failed to emit execution_update: {e}')

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
                    target_room=f'workspace:{workspace_id}',
                    message=f'Failed to handle run completion: {str(e)}',
                    code='COMPLETION_HANDLER_ERROR',
                    context={'run_id': run_id}
                )
            except Exception as emit_err:
                logger.warning(f'Failed to emit error: {emit_err}')

            return None

    async def _reorder_queue(self, workspace_id: str) -> None:
        """Re-order queue positions after promotion."""
        try:
            queue_stmt = select(QueuedRun).where(
                (QueuedRun.workspace_id == workspace_id)
                & (QueuedRun.status == QueuePosition.QUEUED)
            ).order_by(QueuedRun.position)

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

            workspace_id = queued_run.workspace_id
            self.session.delete(queued_run)
            self.session.commit()

            logger.info(f"Cancelled queued run {run_id}")

            # Re-order remaining queue
            await self._reorder_queue(workspace_id)

            return True

        except Exception as e:
            logger.error(f"Error cancelling queued run: {e}", exc_info=True)
            return False

    def get_queue_stats(self, workspace_id: str) -> dict:
        """
        Get queue statistics.

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
            active_stmt = select(AgentSlot).where(
                (AgentSlot.workspace_id == workspace_id)
                & (AgentSlot.is_available == False)
            )
            active_slots = self.session.exec(active_stmt).all()
            active_count = len(active_slots)

            # Count queued runs
            queue_stmt = select(QueuedRun).where(
                (QueuedRun.workspace_id == workspace_id)
                & (QueuedRun.status == QueuePosition.QUEUED)
            )
            queued_runs = self.session.exec(queue_stmt).all()
            queued_count = len(queued_runs)

            # Calculate estimated queue wait time
            queue_wait_minutes = 0
            if queued_runs:
                oldest_queue = min(queued_runs, key=lambda r: r.created_at)
                oldest_age = (datetime.now(timezone.utc) - oldest_queue.created_at).total_seconds()
                queue_wait_minutes = int(oldest_age / 60)

            return {
                "max_concurrent": self.max_concurrent,
                "active_count": active_count,
                "available_slots": self.max_concurrent - active_count,
                "queued_count": queued_count,
                "total_slots_occupied": active_count,
                "queue_wait_time_minutes": queue_wait_minutes,
            }

        except Exception as e:
            logger.error(f"Error getting queue stats: {e}", exc_info=True)
            return {}
