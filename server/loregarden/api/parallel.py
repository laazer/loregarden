"""API endpoints for parallel execution and queue management."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from loregarden.db.session import get_session
from loregarden.models.domain import (
    AgentRun,
    Ticket,
    Worktree,
)
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.parallel_run_service import ParallelRunService
from loregarden.services.queue_status import build_queue_status
from loregarden.services.worktree_lifecycle import RemovalFailure, retire_worktree
from loregarden.services.worktree_service import (
    WorktreeService,
    repo_path_for_worktree,
)
from loregarden.websocket_events import (
    QUEUE_TOPIC,
    emit_error,
    emit_execution_update,
)
from sqlmodel import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parallel", tags=["parallel"])


@router.post("/runs/{ticket_id}")
async def create_parallel_run(
    ticket_id: str = Path(...),
    stage_key: str | None = Query(None),
    slot_number: int | None = Query(None, ge=1),
    auto_approve: bool = Query(False),
    timeout_seconds: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
):
    """
    Create a new run with parallel execution support.

    Either starts immediately if slot available or queues the run.

    Args:
        ticket_id: Ticket ID
        stage_key: Optional stage key to start
        slot_number: Slot the caller staged this ticket into. Honoured when
            free, otherwise the lowest free slot takes it.
        auto_approve: Auto-approve the CLI's permission prompts for this run
        timeout_seconds: Per-run timeout override; None uses the agent default

    Returns:
        {
            "status": "started" | "queued",
            "run": AgentRun (if started),
            "position": int (if queued),
            "message": str,
        }
    """
    try:
        # Get ticket
        ticket = session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        result = await ParallelRunService(session).create_parallel_run(
            ticket,
            stage_key=stage_key,
            preferred_slot=slot_number,
            auto_approve=auto_approve,
            timeout_seconds=timeout_seconds,
        )

        emit_execution_update()

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating parallel run: {e}", exc_info=True)

        # Emit error event
        try:
            ticket = session.get(Ticket, ticket_id)
            if ticket:
                emit_error(
                    target_room=QUEUE_TOPIC,
                    message=f"Failed to create run: {str(e)}",
                    code="RUN_CREATION_ERROR",
                    context={"ticket_id": ticket_id},
                )
        except Exception as emit_err:  # noqa: BLE001 - best-effort emit; logged, real error still raised
            logger.warning(f"Failed to emit error: {emit_err}")

        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/status")
def get_parallel_status(
    session: Session = Depends(get_session),
):
    """
    Get parallel execution status for the shared queue.

    Not scoped to a workspace: the slot pool is global, so a per-workspace view
    would show a slice of a queue whose contention is not sliceable.

    Returns:
        {
            "active_runs": [...],
            "queued_runs": [...],
            "available_slots": 1,
            "total_slots": 3,
            "queue_length": 5,
            "stats": {...},
        }
    """
    try:
        # Shared with the queue websocket, so a client that falls back to
        # polling sees the identical payload rather than a second dialect.
        # Sync handler, so the blocking reads run in the threadpool: as an
        # `async def` this stalled the event loop on every five-second poll.
        return build_queue_status(session)

    except Exception as e:
        logger.error(f"Error getting parallel status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/queue/{run_id}/cancel")
async def cancel_queued_run(
    run_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Cancel a queued run.

    Args:
        run_id: Agent run ID to cancel

    Returns:
        {
            "status": "cancelled",
            "message": str,
        }
    """
    try:
        # Get the run to get workspace_id
        run = session.get(AgentRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        queue_service = ParallelQueueService(session)
        cancelled = await queue_service.cancel_queued_run(run_id)

        if not cancelled:
            raise HTTPException(status_code=404, detail="Queued run not found")

        emit_execution_update()

        return {
            "status": "cancelled",
            "message": f"Run {run_id} cancelled",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling queued run: {e}", exc_info=True)

        # Emit error event
        try:
            run = session.get(AgentRun, run_id)
            if run:
                emit_error(
                    target_room=QUEUE_TOPIC,
                    message=f"Failed to cancel run: {str(e)}",
                    code="RUN_CANCELLATION_ERROR",
                    context={"run_id": run_id},
                )
        except Exception as emit_err:  # noqa: BLE001 - best-effort emit; logged, real error still raised
            logger.warning(f"Failed to emit error: {emit_err}")

        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/worktree/{worktree_id}/cleanup")
def cleanup_worktree(
    worktree_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """Manually clean up a worktree, through the one receipt gate.

    This route used to hand any worktree id straight to the removal seam with
    no premise at all — no receipt, no claim, not a glance at the tree — which
    made it the shortest path to the defect the gate exists to close. It now
    reports what the gate decided, in three shapes a caller can tell apart:

    * 200 — the checkout is gone.
    * 409 — a PREMISE refused and nothing was deleted. The tree is still there
      and still owned; the reason names which premise, so an operator can act
      on it (land the branch, commit the work, wait for the agent).
    * 400 — every premise held and the removal itself did not work. Which of
      the two causes it was is in the detail, because they call for opposite
      responses: a path outside the worktrees folder never clears, and an
      `rmtree` that raised usually clears on the next attempt.
    """
    try:
        worktree = session.get(Worktree, worktree_id)
        if not worktree:
            raise HTTPException(status_code=404, detail="Worktree not found")

        worktree_service = WorktreeService(
            session, repo_path=repo_path_for_worktree(session, worktree)
        )
        outcome = retire_worktree(session, worktree_service, worktree)

        if outcome.refusal is None:
            return {
                "status": "cleaned",
                "message": f"Worktree {worktree_id} cleaned up",
            }
        if outcome.refusal in set(RemovalFailure):
            raise HTTPException(status_code=400, detail=outcome.detail)
        raise HTTPException(
            status_code=409,
            detail=f"{outcome.refusal.value}: {outcome.detail}",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cleaning up worktree: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/active-runs")
def get_active_runs(
    session: Session = Depends(get_session),
):
    """
    Get all active (executing) runs, across every workspace.

    Returns:
        {
            "active_runs": [
                {
                    "run_id": str,
                    "ticket_id": str,
                    "slot_number": int,
                    "elapsed_seconds": int,
                    "status": str,
                },
                ...
            ]
        }
    """
    try:
        queue_service = ParallelQueueService(session)
        active_runs = queue_service.get_active_runs()

        return {"active_runs": active_runs}

    except Exception as e:
        logger.error(f"Error getting active runs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/queued-runs")
def get_queued_runs(
    session: Session = Depends(get_session),
):
    """
    Get all queued (waiting) runs, across every workspace.

    Returns:
        {
            "queued_runs": [
                {
                    "run_id": str,
                    "ticket_id": str,
                    "position": int,
                    "estimated_start_at": str,
                    "wait_seconds": int,
                },
                ...
            ]
        }
    """
    try:
        queue_service = ParallelQueueService(session)
        queued_runs = queue_service.get_queued_runs()

        return {"queued_runs": queued_runs}

    except Exception as e:
        logger.error(f"Error getting queued runs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
