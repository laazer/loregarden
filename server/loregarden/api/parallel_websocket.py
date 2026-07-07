"""
WebSocket-enhanced parallel API endpoints.
Shows example of how to integrate event emissions into existing endpoints.

This file demonstrates the pattern - integrate into existing api/parallel.py
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlmodel import Session, select

from loregarden.db.session import get_session
from loregarden.models.domain import AgentRun, Ticket, WorktreeState
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.websocket_events import (
    emit_execution_update,
    emit_error,
    emit_run_completed,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parallel", tags=["parallel"])


# EXAMPLE 1: Enhanced create_parallel_run with event emission
@router.post("/runs/{ticket_id}")
async def create_parallel_run_ws(
    ticket_id: str = Path(...),
    stage_key: Optional[str] = Query(None),
    max_concurrent: int = Query(3),
    session: Session = Depends(get_session),
):
    """
    Create a new run with WebSocket event emission.

    Events emitted:
    - execution_update (if run starts immediately)
    - execution_update (if run is queued)
    """
    try:
        # Get ticket
        ticket = session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        # Create parallel run
        orchestration = OrchestrationService(session)
        result = await orchestration.create_parallel_run(
            ticket,
            stage_key=stage_key,
            max_concurrent=max_concurrent,
        )

        # NEW: Emit execution update to WebSocket subscribers
        try:
            queue_service = ParallelQueueService(session, max_concurrent=max_concurrent)

            # Get current state
            active_runs = await queue_service.get_active_runs(ticket.workspace_id)
            queued_runs = await queue_service.get_queued_runs(ticket.workspace_id)
            stats = await queue_service.get_stats(ticket.workspace_id)

            # Broadcast to all workspace subscribers
            emit_execution_update(
                workspace_id=ticket.workspace_id,
                active_runs=active_runs,
                queued_runs=queued_runs,
                stats=stats,
            )

            logger.info(
                f'Emitted execution_update for new run in {ticket.workspace_id}',
                extra={'status': result.get('status')}
            )
        except Exception as e:
            logger.warning(f'Failed to emit execution_update: {e}')
            # Don't fail the request if WebSocket emission fails

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating parallel run: {e}", exc_info=True)

        # NEW: Emit error event
        try:
            emit_error(
                target_room=f'workspace:{ticket.workspace_id}',
                message=f'Failed to create run: {str(e)}',
                code='RUN_CREATION_ERROR',
                context={'ticket_id': ticket_id}
            )
        except Exception as emit_err:
            logger.warning(f'Failed to emit error: {emit_err}')

        raise HTTPException(status_code=500, detail=str(e))


# EXAMPLE 2: Enhanced run completion with event emission
@router.post("/runs/{run_id}/complete")
async def complete_run_ws(
    run_id: str = Path(...),
    status: str = Query(...),
    session: Session = Depends(get_session),
):
    """
    Mark a run as complete with WebSocket event emission.

    Events emitted:
    - run_completed
    - execution_update
    - queue_promoted (if queue has waiting runs)
    """
    try:
        # Get run
        run = session.get(AgentRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        # Update run status
        run.status = status
        session.add(run)
        session.commit()

        logger.info(f'Run {run_id} marked as {status}')

        # NEW: Emit run completed event
        try:
            emit_run_completed(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=status,
            )
        except Exception as e:
            logger.warning(f'Failed to emit run_completed: {e}')

        # Promote from queue if applicable
        try:
            queue_service = ParallelQueueService(session)
            await queue_service.on_run_complete(run)
            # on_run_complete already emits events
        except Exception as e:
            logger.warning(f'Failed to promote from queue: {e}')

        # NEW: Emit updated execution status
        try:
            queue_service = ParallelQueueService(session)
            active_runs = await queue_service.get_active_runs(run.workspace_id)
            queued_runs = await queue_service.get_queued_runs(run.workspace_id)
            stats = await queue_service.get_stats(run.workspace_id)

            emit_execution_update(
                workspace_id=run.workspace_id,
                active_runs=active_runs,
                queued_runs=queued_runs,
                stats=stats,
            )

            logger.info(f'Emitted execution_update after run completion')
        except Exception as e:
            logger.warning(f'Failed to emit execution_update: {e}')

        return {
            'status': 'completed',
            'run_id': run_id,
            'final_status': status,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing run: {e}", exc_info=True)

        # NEW: Emit error event
        try:
            run = session.get(AgentRun, run_id)
            if run:
                emit_error(
                    target_room=f'workspace:{run.workspace_id}',
                    message=f'Failed to complete run: {str(e)}',
                    code='RUN_COMPLETION_ERROR',
                    context={'run_id': run_id}
                )
        except Exception as emit_err:
            logger.warning(f'Failed to emit error: {emit_err}')

        raise HTTPException(status_code=500, detail=str(e))


# EXAMPLE 3: Enhanced cancel run with event emission
@router.post("/runs/{run_id}/cancel")
async def cancel_run_ws(
    run_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Cancel a run with WebSocket event emission.

    Events emitted:
    - execution_update
    - queue_promoted (if run was active and queue had waiting)
    """
    try:
        # Get run
        run = session.get(AgentRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        workspace_id = run.workspace_id

        # Cancel the run
        if run.status in ['queued', 'scheduled']:
            run.status = 'cancelled'
            session.add(run)
            session.commit()

            logger.info(f'Run {run_id} cancelled')

            # NEW: Emit updated execution status
            try:
                queue_service = ParallelQueueService(session)
                active_runs = await queue_service.get_active_runs(workspace_id)
                queued_runs = await queue_service.get_queued_runs(workspace_id)
                stats = await queue_service.get_stats(workspace_id)

                emit_execution_update(
                    workspace_id=workspace_id,
                    active_runs=active_runs,
                    queued_runs=queued_runs,
                    stats=stats,
                )
            except Exception as e:
                logger.warning(f'Failed to emit execution_update: {e}')
        else:
            raise HTTPException(status_code=400, detail="Can only cancel queued runs")

        return {
            'status': 'cancelled',
            'run_id': run_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling run: {e}", exc_info=True)

        # NEW: Emit error event
        try:
            emit_error(
                target_room=f'workspace:{run.workspace_id}',
                message=f'Failed to cancel run: {str(e)}',
                code='RUN_CANCELLATION_ERROR',
                context={'run_id': run_id}
            )
        except Exception as emit_err:
            logger.warning(f'Failed to emit error: {emit_err}')

        raise HTTPException(status_code=500, detail=str(e))


# EXAMPLE 4: No changes needed to status endpoint
# The GET /status/{workspace_id} endpoint is still used by polling clients
# WebSocket clients don't call it, they get events instead
# No event emissions needed here, as this is for polling clients
