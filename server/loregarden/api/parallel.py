"""API endpoints for parallel execution, queue management, and conflict detection."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from loregarden.db.session import get_session
from loregarden.models.domain import (
    AgentRun,
    Ticket,
    Worktree,
    WorktreeState,
)
from loregarden.services.conflict_detector import ConflictDetectorService
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.queue_status import build_queue_status
from loregarden.services.worktree_service import WorktreeService
from loregarden.websocket_events import (
    emit_conflict_detected,
    emit_conflict_resolved,
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
    max_concurrent: int = Query(3),
    session: Session = Depends(get_session),
):
    """
    Create a new run with parallel execution support.

    Either starts immediately if slot available or queues the run.

    Args:
        ticket_id: Ticket ID
        stage_key: Optional stage key to start
        max_concurrent: Max concurrent runs (default 3)

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

        # Create parallel run
        orchestration = OrchestrationService(session)
        result = await orchestration.create_parallel_run(
            ticket,
            stage_key=stage_key,
            max_concurrent=max_concurrent,
        )

        emit_execution_update(ticket.workspace_id)

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
                    target_room=f"workspace:{ticket.workspace_id}",
                    message=f"Failed to create run: {str(e)}",
                    code="RUN_CREATION_ERROR",
                    context={"ticket_id": ticket_id},
                )
        except Exception as emit_err:
            logger.warning(f"Failed to emit error: {emit_err}")

        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/status/{workspace_id}")
async def get_parallel_status(
    workspace_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Get parallel execution status for a workspace.

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
        return await build_queue_status(session, workspace_id)

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

        emit_execution_update(run.workspace_id)

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
                    target_room=f"workspace:{run.workspace_id}",
                    message=f"Failed to cancel run: {str(e)}",
                    code="RUN_CANCELLATION_ERROR",
                    context={"run_id": run_id},
                )
        except Exception as emit_err:
            logger.warning(f"Failed to emit error: {emit_err}")

        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/conflicts/{worktree_id}")
async def check_conflicts(
    worktree_id: str = Path(...),
    target_branch: str = Query("main"),
    session: Session = Depends(get_session),
):
    """
    Check for merge conflicts in a worktree.

    Args:
        worktree_id: Worktree ID
        target_branch: Branch to check against (default: main)

    Returns:
        {
            "has_conflicts": bool,
            "conflicting_files": list[str],
            "summary": str,
            "auto_mergeable": bool,
            "severity": str,
            "suggestions": list[str],
        }
    """
    try:
        # Get worktree
        worktree = session.get(Worktree, worktree_id)
        if not worktree:
            raise HTTPException(status_code=404, detail="Worktree not found")

        # Get conflict preview
        conflict_service = ConflictDetectorService(session, repo_path=".")
        preview = await conflict_service.get_conflict_preview(worktree, target_branch)

        if not preview.get("has_conflicts"):
            return {
                "has_conflicts": False,
                "conflicting_files": [],
                "summary": preview.get("summary", "Clean merge"),
                "auto_mergeable": True,
                "severity": "low",
                "suggestions": ["Ready to merge"],
            }

        # Get detailed conflict information
        details = await conflict_service.get_conflict_details(worktree, target_branch)

        # Emit conflict detected event
        try:
            emit_conflict_detected(
                worktree_id=worktree_id,
                run_id=worktree.agent_run_id,
                conflicts=details.get("conflicts", []),
                preview=preview,
                severity=details.get("severity", "medium"),
            )
        except Exception as e:
            logger.warning(f"Failed to emit conflict_detected: {e}")

        return {
            "has_conflicts": True,
            "conflicting_files": preview.get("conflicting_files", []),
            "summary": preview.get("summary"),
            "auto_mergeable": preview.get("auto_mergeable", False),
            "severity": details.get("severity", "medium"),
            "suggestions": details.get("suggestions", []),
            "conflicts": details.get("conflicts", []),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking conflicts: {e}", exc_info=True)

        # Emit error event
        try:
            emit_error(
                target_room=f"worktree:{worktree_id}",
                message=f"Failed to check conflicts: {str(e)}",
                code="CONFLICT_CHECK_ERROR",
                context={"worktree_id": worktree_id},
            )
        except Exception as emit_err:
            logger.warning(f"Failed to emit error: {emit_err}")

        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/worktree/{worktree_id}/merge")
async def merge_worktree(
    worktree_id: str = Path(...),
    target_branch: str = Query("main"),
    auto_resolve: bool = Query(False),
    session: Session = Depends(get_session),
):
    """
    Merge a worktree back to target branch.

    Args:
        worktree_id: Worktree ID
        target_branch: Branch to merge into (default: main)
        auto_resolve: Whether to auto-resolve conflicts (default False)

    Returns:
        {
            "status": "merged" | "conflicts",
            "message": str,
            "conflict_files": list[str] (if conflicts),
        }
    """
    try:
        # Get worktree
        worktree = session.get(Worktree, worktree_id)
        if not worktree:
            raise HTTPException(status_code=404, detail="Worktree not found")

        if worktree.state != WorktreeState.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot merge worktree in state: {worktree.state.value}",
            )

        # Merge worktree
        worktree_service = WorktreeService(session, repo_path=".")
        success = worktree_service.merge_worktree(
            worktree,
            target_branch=target_branch,
            auto_resolve=auto_resolve,
        )

        if success:
            # Emit conflict resolved event on successful merge
            try:
                emit_conflict_resolved(
                    worktree_id=worktree_id,
                    run_id=worktree.agent_run_id,
                )
            except Exception as e:
                logger.warning(f"Failed to emit conflict_resolved: {e}")

            return {
                "status": "merged",
                "message": f"Worktree merged to {target_branch}",
            }
        else:
            # Get conflict details
            if worktree.has_conflicts:
                # Emit error event for merge failure with remaining conflicts
                try:
                    emit_error(
                        target_room=f"worktree:{worktree_id}",
                        message=f"Merge failed with {len(worktree.conflict_files)} conflicting files",
                        code="MERGE_FAILED_WITH_CONFLICTS",
                        context={
                            "worktree_id": worktree_id,
                            "conflict_count": len(worktree.conflict_files),
                            "conflict_files": worktree.conflict_files,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to emit error: {e}")

                return {
                    "status": "conflicts",
                    "message": f"Merge conflicts in {len(worktree.conflict_files)} files",
                    "conflict_files": worktree.conflict_files,
                }
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Merge failed without conflicts",
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error merging worktree: {e}", exc_info=True)

        # Emit error event
        try:
            worktree = session.get(Worktree, worktree_id)
            if worktree:
                emit_error(
                    target_room=f"worktree:{worktree_id}",
                    message=f"Failed to merge worktree: {str(e)}",
                    code="MERGE_ERROR",
                    context={"worktree_id": worktree_id},
                )
        except Exception as emit_err:
            logger.warning(f"Failed to emit error: {emit_err}")

        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/worktree/{worktree_id}/cleanup")
async def cleanup_worktree(
    worktree_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Manually cleanup a worktree.

    Args:
        worktree_id: Worktree ID

    Returns:
        {
            "status": "cleaned",
            "message": str,
        }
    """
    try:
        # Get worktree
        worktree = session.get(Worktree, worktree_id)
        if not worktree:
            raise HTTPException(status_code=404, detail="Worktree not found")

        # Cleanup worktree
        worktree_service = WorktreeService(session, repo_path=".")
        success = worktree_service.cleanup_worktree(worktree)

        if success:
            return {
                "status": "cleaned",
                "message": f"Worktree {worktree_id} cleaned up",
            }
        else:
            raise HTTPException(
                status_code=400,
                detail="Cleanup failed",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cleaning up worktree: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/worktree/{worktree_id}")
async def get_worktree_details(
    worktree_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Get detailed information about a worktree.

    Args:
        worktree_id: Worktree ID

    Returns:
        {
            "id": str,
            "state": str,
            "path": str,
            "has_conflicts": bool,
            "conflict_files": list[str],
            "agent_run_id": str,
            "created_at": str,
            "merged_at": str (optional),
        }
    """
    try:
        # Get worktree
        worktree = session.get(Worktree, worktree_id)
        if not worktree:
            raise HTTPException(status_code=404, detail="Worktree not found")

        return {
            "id": worktree.id,
            "state": worktree.state.value,
            "path": worktree.worktree_path,
            "has_conflicts": worktree.has_conflicts,
            "conflict_files": worktree.conflict_files,
            "agent_run_id": worktree.agent_run_id,
            "parent_branch": worktree.parent_branch,
            "created_at": worktree.created_at.isoformat() if worktree.created_at else None,
            "merged_at": worktree.merged_at.isoformat() if worktree.merged_at else None,
            "cleaned_at": worktree.cleaned_at.isoformat() if worktree.cleaned_at else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting worktree details: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/conflict-reports/{worktree_id}")
async def get_conflict_reports(
    worktree_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Get conflict reports for a worktree.

    Args:
        worktree_id: Worktree ID

    Returns:
        {
            "reports": [
                {
                    "id": str,
                    "created_at": str,
                    "conflicting_files": list[str],
                    "resolution_successful": bool,
                },
                ...
            ]
        }
    """
    try:
        # Get conflict reports
        conflict_service = ConflictDetectorService(session, repo_path=".")
        reports = conflict_service.get_worktree_conflicts(worktree_id)

        return {
            "reports": [
                {
                    "id": report.id,
                    "created_at": report.created_at.isoformat() if report.created_at else None,
                    "conflicting_files": report.conflicting_files,
                    "resolution_successful": report.resolution_successful,
                    "conflict_type": report.conflict_type,
                }
                for report in reports
            ]
        }

    except Exception as e:
        logger.error(f"Error getting conflict reports: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/active-runs/{workspace_id}")
async def get_active_runs(
    workspace_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Get all active (executing) runs for a workspace.

    Args:
        workspace_id: Workspace ID

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
        active_runs = await queue_service.get_active_runs(workspace_id)

        return {"active_runs": active_runs}

    except Exception as e:
        logger.error(f"Error getting active runs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/queued-runs/{workspace_id}")
async def get_queued_runs(
    workspace_id: str = Path(...),
    session: Session = Depends(get_session),
):
    """
    Get all queued (waiting) runs for a workspace.

    Args:
        workspace_id: Workspace ID

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
        queued_runs = await queue_service.get_queued_runs(workspace_id)

        return {"queued_runs": queued_runs}

    except Exception as e:
        logger.error(f"Error getting queued runs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
