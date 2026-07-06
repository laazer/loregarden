"""
WebSocket-enhanced ParallelQueueService example.
Shows how to add event emissions to existing queue management methods.

This file demonstrates the pattern - integrate into existing services/parallel_queue.py
"""

import logging
from typing import List, Optional
from datetime import datetime, timedelta

from loregarden.models.domain import AgentRun, QueuedRun
from loregarden.websocket_events import (
    emit_execution_update,
    emit_queue_promoted,
    emit_run_completed,
    emit_error,
)

logger = logging.getLogger(__name__)


# EXAMPLE 1: Enhanced queue_run method with event emission
async def queue_run_ws(
    self,  # self from ParallelQueueService
    run: AgentRun,
    workspace_id: str,
) -> QueuedRun:
    """
    Queue a run when no execution slots available.

    Events emitted:
    - execution_update: When run is queued
    """
    try:
        # Existing queueing logic would go here
        # ... create QueuedRun record ...
        # queued_run = QueuedRun(...)
        # self.session.add(queued_run)
        # self.session.commit()

        logger.info(f'Run {run.run_id} queued in workspace {workspace_id}')

        # NEW: Emit execution update to show new queue state
        try:
            active_runs = await self.get_active_runs(workspace_id)
            queued_runs = await self.get_queued_runs(workspace_id)
            stats = await self.get_stats(workspace_id)

            emit_execution_update(
                workspace_id=workspace_id,
                active_runs=active_runs,
                queued_runs=queued_runs,
                stats=stats,
            )

            logger.debug(f'Emitted execution_update for queue in {workspace_id}')
        except Exception as e:
            logger.warning(f'Failed to emit execution_update: {e}')

        # return queued_run

    except Exception as e:
        logger.error(f'Error queuing run: {e}', exc_info=True)

        # NEW: Emit error event
        try:
            emit_error(
                target_room=f'workspace:{workspace_id}',
                message=f'Failed to queue run: {str(e)}',
                code='QUEUE_ERROR',
                context={'run_id': run.run_id}
            )
        except Exception as emit_err:
            logger.warning(f'Failed to emit error: {emit_err}')

        raise


# EXAMPLE 2: Enhanced promote_from_queue with event emission
async def promote_from_queue_ws(
    self,  # self from ParallelQueueService
    workspace_id: str,
) -> Optional[AgentRun]:
    """
    Promote next queued run to available execution slot.

    Events emitted:
    - queue_promoted: When a run moves to active slot
    - execution_update: Updated state with promoted run
    """
    try:
        # Existing promotion logic would go here
        # ... get next queued run ...
        # ... assign to available slot ...
        # promoted_run = ...

        promoted_run = None  # placeholder

        if promoted_run:
            logger.info(
                f'Run {promoted_run.run_id} promoted to slot {promoted_run.slot_number}'
            )

            # NEW: Emit queue promotion event
            try:
                emit_queue_promoted(
                    workspace_id=workspace_id,
                    run_id=promoted_run.run_id,
                    slot_number=promoted_run.slot_number,
                )
                logger.debug(f'Emitted queue_promoted for {promoted_run.run_id}')
            except Exception as e:
                logger.warning(f'Failed to emit queue_promoted: {e}')

            # NEW: Emit updated execution status
            try:
                active_runs = await self.get_active_runs(workspace_id)
                queued_runs = await self.get_queued_runs(workspace_id)
                stats = await self.get_stats(workspace_id)

                emit_execution_update(
                    workspace_id=workspace_id,
                    active_runs=active_runs,
                    queued_runs=queued_runs,
                    stats=stats,
                )

                logger.debug(f'Emitted execution_update after promotion in {workspace_id}')
            except Exception as e:
                logger.warning(f'Failed to emit execution_update: {e}')

        return promoted_run

    except Exception as e:
        logger.error(f'Error promoting from queue: {e}', exc_info=True)

        # NEW: Emit error event
        try:
            emit_error(
                target_room=f'workspace:{workspace_id}',
                message=f'Failed to promote from queue: {str(e)}',
                code='PROMOTION_ERROR',
                context={'workspace_id': workspace_id}
            )
        except Exception as emit_err:
            logger.warning(f'Failed to emit error: {emit_err}')

        raise


# EXAMPLE 3: Enhanced on_run_complete with event emission
async def on_run_complete_ws(
    self,  # self from ParallelQueueService
    run: AgentRun,
) -> None:
    """
    Handle run completion: free slot, promote from queue.

    Events emitted:
    - run_completed: Run finished
    - queue_promoted: Next run takes slot (if queue not empty)
    - execution_update: Final state update
    """
    try:
        workspace_id = run.workspace_id
        run_id = run.run_id
        status = run.status

        logger.info(f'Run {run_id} completed with status {status}')

        # Existing logic: free the slot, update database
        # ... mark slot as available ...
        # ... move run to completed section ...
        # self.session.commit()

        # NEW: Emit run completed event
        try:
            emit_run_completed(
                workspace_id=workspace_id,
                run_id=run_id,
                status=status,
            )
            logger.debug(f'Emitted run_completed for {run_id}')
        except Exception as e:
            logger.warning(f'Failed to emit run_completed: {e}')

        # Try to promote next queued run
        try:
            promoted = await self.promote_from_queue(workspace_id)
            # promote_from_queue already emits events
            if promoted:
                logger.info(f'Promoted {promoted.run_id} to fill freed slot')
        except Exception as e:
            logger.warning(f'Failed to promote from queue: {e}')

        # NEW: Emit final execution status
        try:
            active_runs = await self.get_active_runs(workspace_id)
            queued_runs = await self.get_queued_runs(workspace_id)
            stats = await self.get_stats(workspace_id)

            emit_execution_update(
                workspace_id=workspace_id,
                active_runs=active_runs,
                queued_runs=queued_runs,
                stats=stats,
            )

            logger.debug(f'Emitted execution_update after completion in {workspace_id}')
        except Exception as e:
            logger.warning(f'Failed to emit execution_update: {e}')

    except Exception as e:
        logger.error(f'Error handling run completion: {e}', exc_info=True)

        # NEW: Emit error event
        try:
            emit_error(
                target_room=f'workspace:{run.workspace_id}',
                message=f'Failed to handle run completion: {str(e)}',
                code='COMPLETION_HANDLER_ERROR',
                context={'run_id': run.run_id}
            )
        except Exception as emit_err:
            logger.warning(f'Failed to emit error: {emit_err}')

        raise


# EXAMPLE 4: Get stats method (no event emission needed)
async def get_stats_ws(
    self,  # self from ParallelQueueService
    workspace_id: str,
) -> dict:
    """
    Get queue statistics.

    No event emissions here - called by status endpoint and from other methods.
    """
    # Existing implementation
    # return {
    #     'max_concurrent': self.max_concurrent,
    #     'active_count': active_count,
    #     'available_slots': available_slots,
    #     'queued_count': queued_count,
    #     'total_slots_occupied': active_count,
    #     'queue_wait_time_minutes': wait_time_minutes,
    # }
    pass
