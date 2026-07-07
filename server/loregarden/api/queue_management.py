"""API endpoints for queue management and reordering."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlmodel import Session, select

from loregarden.db.session import get_session
from loregarden.models.domain import QueuedRun, QueuePosition, AgentRun
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.websocket_events import (
    emit_execution_update,
    emit_error,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parallel/queue", tags=["queue"])


@router.post("/{run_id}/reorder")
async def reorder_queued_run(
    run_id: str = Path(...),
    new_position: int = Query(...),
    session: Session = Depends(get_session),
):
    """
    Reorder a queued run to a new position in the queue.

    Args:
        run_id: ID of the queued run to move
        new_position: New position in queue (1-indexed, 1 = first)

    Returns:
        {
            "status": "reordered",
            "run_id": str,
            "old_position": int,
            "new_position": int,
            "message": str,
        }
    """
    try:
        # Get the queued run
        stmt = select(QueuedRun).where(QueuedRun.run_id == run_id)
        queued_run = session.exec(stmt).first()

        if not queued_run:
            raise HTTPException(status_code=404, detail="Queued run not found")

        if queued_run.status != QueuePosition.QUEUED:
            raise HTTPException(
                status_code=400,
                detail=f"Run is not queued (status: {queued_run.status.value})",
            )

        workspace_id = queued_run.workspace_id
        old_position = queued_run.position

        # Validate new position
        queue_stmt = select(QueuedRun).where(
            (QueuedRun.workspace_id == workspace_id)
            & (QueuedRun.status == QueuePosition.QUEUED)
        )
        queued_runs = session.exec(queue_stmt).all()
        queue_length = len(queued_runs)

        if new_position < 1 or new_position > queue_length:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid position {new_position}. Queue length: {queue_length}",
            )

        # If position hasn't changed, return early
        if old_position == new_position:
            return {
                "status": "no_change",
                "run_id": run_id,
                "position": old_position,
                "message": f"Run already at position {old_position}",
            }

        # Reorder the queue
        await _reorder_queue_internal(
            session,
            workspace_id,
            run_id,
            old_position,
            new_position,
        )

        logger.info(
            f"Reordered run {run_id} from position {old_position} to {new_position} "
            f"in workspace {workspace_id}"
        )

        # Emit execution update to show new queue state
        try:
            queue_service = ParallelQueueService(session)
            active_runs = await queue_service.get_active_runs(workspace_id)
            queued_runs = await queue_service.get_queued_runs(workspace_id)
            stats = queue_service.get_queue_stats(workspace_id)

            emit_execution_update(
                workspace_id=workspace_id,
                active_runs=active_runs,
                queued_runs=queued_runs,
                stats=stats,
            )

            logger.debug(f"Emitted execution_update after reorder in {workspace_id}")
        except Exception as e:
            logger.warning(f"Failed to emit execution_update: {e}")

        return {
            "status": "reordered",
            "run_id": run_id,
            "old_position": old_position,
            "new_position": new_position,
            "message": f"Run moved from position {old_position} to {new_position}",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reordering queue: {e}", exc_info=True)

        # Emit error event
        try:
            queued_run = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()
            if queued_run:
                emit_error(
                    target_room=f'workspace:{queued_run.workspace_id}',
                    message=f'Failed to reorder run: {str(e)}',
                    code='QUEUE_REORDER_ERROR',
                    context={'run_id': run_id, 'new_position': new_position}
                )
        except Exception as emit_err:
            logger.warning(f"Failed to emit error: {emit_err}")

        raise HTTPException(status_code=500, detail=str(e))


async def _reorder_queue_internal(
    session: Session,
    workspace_id: str,
    run_id: str,
    old_position: int,
    new_position: int,
) -> None:
    """
    Internal function to reorder queue positions.

    Args:
        session: Database session
        workspace_id: Workspace ID
        run_id: Run ID to move
        old_position: Current position
        new_position: Target position
    """
    # Get all queued runs ordered by position
    stmt = select(QueuedRun).where(
        (QueuedRun.workspace_id == workspace_id)
        & (QueuedRun.status == QueuePosition.QUEUED)
    ).order_by(QueuedRun.position)

    queued_runs = session.exec(stmt).all()
    queue_dict = {qr.run_id: qr for qr in queued_runs}

    # Perform reorder logic
    if old_position < new_position:
        # Moving down: runs between old and new move up
        for qr in queued_runs:
            if old_position < qr.position <= new_position:
                qr.position -= 1
                session.add(qr)
    else:
        # Moving up: runs between new and old move down
        for qr in queued_runs:
            if new_position <= qr.position < old_position:
                qr.position += 1
                session.add(qr)

    # Set target run to new position
    target_run = queue_dict[run_id]
    target_run.position = new_position
    session.add(target_run)

    session.commit()

    logger.debug(
        f"Queue reordered in {workspace_id}: "
        f"run {run_id} from pos {old_position} to {new_position}"
    )


@router.get("/{workspace_id}/info")
async def get_queue_info(
    workspace_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Get detailed queue information for a workspace.

    Args:
        workspace_id: Workspace ID

    Returns:
        {
            "workspace_id": str,
            "queue_length": int,
            "max_position": int,
            "runs": [
                {
                    "run_id": str,
                    "ticket_id": str,
                    "position": int,
                    "wait_seconds": int,
                },
                ...
            ],
            "estimated_clear_time_seconds": int,
        }
    """
    try:
        queue_service = ParallelQueueService(session)

        # Get queue data
        queued_runs = await queue_service.get_queued_runs(workspace_id)
        active_runs = await queue_service.get_active_runs(workspace_id)

        # Calculate estimated clear time
        # Assume 5 minutes per active run, 5 minutes per queued run
        active_time = len(active_runs) * 300
        queued_time = len(queued_runs) * 300
        estimated_clear = active_time + queued_time

        return {
            "workspace_id": workspace_id,
            "queue_length": len(queued_runs),
            "max_position": len(queued_runs),
            "runs": queued_runs,
            "active_runs_count": len(active_runs),
            "estimated_clear_time_seconds": estimated_clear,
        }

    except Exception as e:
        logger.error(f"Error getting queue info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{run_id}/promote")
async def promote_run(
    run_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Manually promote a queued run to available slot (if any).

    Args:
        run_id: ID of queued run to promote

    Returns:
        {
            "status": "promoted" | "no_slots" | "not_found",
            "message": str,
            "promoted_run": {...} (if promoted),
        }
    """
    try:
        # Get the queued run
        stmt = select(QueuedRun).where(QueuedRun.run_id == run_id)
        queued_run = session.exec(stmt).first()

        if not queued_run:
            raise HTTPException(status_code=404, detail="Queued run not found")

        workspace_id = queued_run.workspace_id

        # Try to promote
        queue_service = ParallelQueueService(session)
        promoted = await queue_service.promote_from_queue(workspace_id)

        if not promoted or promoted["run_id"] != run_id:
            return {
                "status": "no_slots",
                "message": "No available slots for promotion",
            }

        logger.info(f"Manually promoted run {run_id} in workspace {workspace_id}")

        return {
            "status": "promoted",
            "message": f"Run promoted to slot {promoted['slot_number']}",
            "promoted_run": promoted,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error promoting run: {e}", exc_info=True)

        try:
            queued_run = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()
            if queued_run:
                emit_error(
                    target_room=f'workspace:{queued_run.workspace_id}',
                    message=f'Failed to promote run: {str(e)}',
                    code='QUEUE_PROMOTION_ERROR',
                    context={'run_id': run_id}
                )
        except Exception as emit_err:
            logger.warning(f"Failed to emit error: {emit_err}")

        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{run_id}/pause")
async def pause_run(
    run_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Pause an active run, returning its slot for the next queued run.

    Args:
        run_id: ID of active run to pause

    Returns:
        {
            "status": "paused" | "not_found" | "not_active",
            "message": str,
        }
    """
    try:
        stmt = select(QueuedRun).where(QueuedRun.run_id == run_id)
        run = session.exec(stmt).first()

        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        if run.status != QueuePosition.ACTIVE:
            raise HTTPException(status_code=400, detail="Run is not active")

        workspace_id = run.workspace_id

        # Mark as paused
        run.status = "paused"
        session.add(run)
        session.commit()

        logger.info(f"Paused run {run_id} in workspace {workspace_id}")

        # Emit update event
        try:
            queue_service = ParallelQueueService(session)
            active_runs = await queue_service.get_active_runs(workspace_id)
            queued_runs = await queue_service.get_queued_runs(workspace_id)
            stats = queue_service.get_queue_stats(workspace_id)

            emit_execution_update(
                workspace_id=workspace_id,
                active_runs=active_runs,
                queued_runs=queued_runs,
                stats=stats,
            )
        except Exception as e:
            logger.warning(f"Failed to emit update: {e}")

        return {
            "status": "paused",
            "run_id": run_id,
            "message": f"Run paused",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pausing run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{run_id}/resume")
async def resume_run(
    run_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Resume a paused run.

    Args:
        run_id: ID of paused run to resume

    Returns:
        {
            "status": "resumed" | "not_found" | "not_paused",
            "message": str,
        }
    """
    try:
        stmt = select(QueuedRun).where(QueuedRun.run_id == run_id)
        run = session.exec(stmt).first()

        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        if run.status != "paused":
            raise HTTPException(status_code=400, detail="Run is not paused")

        workspace_id = run.workspace_id

        # Mark as active again
        run.status = QueuePosition.ACTIVE
        session.add(run)
        session.commit()

        logger.info(f"Resumed run {run_id} in workspace {workspace_id}")

        # Emit update event
        try:
            queue_service = ParallelQueueService(session)
            active_runs = await queue_service.get_active_runs(workspace_id)
            queued_runs = await queue_service.get_queued_runs(workspace_id)
            stats = queue_service.get_queue_stats(workspace_id)

            emit_execution_update(
                workspace_id=workspace_id,
                active_runs=active_runs,
                queued_runs=queued_runs,
                stats=stats,
            )
        except Exception as e:
            logger.warning(f"Failed to emit update: {e}")

        return {
            "status": "resumed",
            "run_id": run_id,
            "message": f"Run resumed",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Cancel an active or queued run.

    Args:
        run_id: ID of run to cancel

    Returns:
        {
            "status": "cancelled" | "not_found",
            "message": str,
        }
    """
    try:
        stmt = select(QueuedRun).where(QueuedRun.run_id == run_id)
        run = session.exec(stmt).first()

        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        workspace_id = run.workspace_id
        was_active = run.status == QueuePosition.ACTIVE

        # Mark as cancelled
        run.status = "cancelled"
        session.add(run)
        session.commit()

        logger.info(f"Cancelled run {run_id} in workspace {workspace_id}")

        # If it was active, promote next from queue
        if was_active:
            queue_service = ParallelQueueService(session)
            try:
                await queue_service.promote_from_queue(workspace_id)
            except Exception as e:
                logger.warning(f"Failed to promote next run: {e}")

        # Emit update event
        try:
            queue_service = ParallelQueueService(session)
            active_runs = await queue_service.get_active_runs(workspace_id)
            queued_runs = await queue_service.get_queued_runs(workspace_id)
            stats = queue_service.get_queue_stats(workspace_id)

            emit_execution_update(
                workspace_id=workspace_id,
                active_runs=active_runs,
                queued_runs=queued_runs,
                stats=stats,
            )
        except Exception as e:
            logger.warning(f"Failed to emit update: {e}")

        return {
            "status": "cancelled",
            "run_id": run_id,
            "message": f"Run cancelled",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
