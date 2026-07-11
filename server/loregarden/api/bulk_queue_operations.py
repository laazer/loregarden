"""Bulk queue operations: cancel/pause/reorder multiple runs at once."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from loregarden.api.queue_management import emit_execution_update
from loregarden.db.session import get_session
from loregarden.models.domain import QueuedRun, QueuePosition
from sqlmodel import Session, select

router = APIRouter(prefix="/api/parallel", tags=["bulk-operations"])


class BulkOperationRequest:
    """Request for bulk queue operations."""
    operation: str  # "cancel", "pause", "resume", "retry"
    run_ids: list[str]
    filters: dict | None = None  # Optional: filter by status, position range, etc


class BulkOperationResponse:
    """Response from bulk operation."""
    operation: str
    total_requested: int
    successful: int
    failed: int
    results: list[dict]  # Per-run results with status/error


@router.post("/workspace/{workspace_id}/queue/bulk-cancel")
async def bulk_cancel_runs(
    workspace_id: str,
    run_ids: list[str],
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> BulkOperationResponse:
    """Cancel multiple runs at once."""
    results = []
    successful = 0
    failed = 0

    for run_id in run_ids:
        try:
            run = session.exec(
                select(QueuedRun).where(
                    (QueuedRun.run_id == run_id)
                    & (QueuedRun.workspace_id == workspace_id)
                )
            ).first()

            if not run:
                results.append(
                    {"run_id": run_id, "status": "error", "message": "Run not found"}
                )
                failed += 1
                continue

            run.status = QueuePosition.QUEUED  # Mark as cancelled state
            # Can also add a cancelled_at field if needed
            session.add(run)
            session.commit()

            results.append({"run_id": run_id, "status": "cancelled"})
            successful += 1

            if background_tasks:
                background_tasks.add_task(
                    emit_execution_update,
                    workspace_id,
                    {"type": "bulk_cancel", "run_id": run_id},
                )

        except Exception as e:
            results.append(
                {"run_id": run_id, "status": "error", "message": str(e)}
            )
            failed += 1
            session.rollback()

    return BulkOperationResponse(
        operation="cancel",
        total_requested=len(run_ids),
        successful=successful,
        failed=failed,
        results=results,
    )


@router.post("/workspace/{workspace_id}/queue/bulk-pause")
async def bulk_pause_runs(
    workspace_id: str,
    run_ids: list[str],
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> BulkOperationResponse:
    """Pause multiple active runs at once."""
    results = []
    successful = 0
    failed = 0

    for run_id in run_ids:
        try:
            run = session.exec(
                select(QueuedRun).where(
                    (QueuedRun.run_id == run_id)
                    & (QueuedRun.workspace_id == workspace_id)
                    & (QueuedRun.status == QueuePosition.STARTED)
                )
            ).first()

            if not run:
                results.append(
                    {
                        "run_id": run_id,
                        "status": "error",
                        "message": "Run not found or not active",
                    }
                )
                failed += 1
                continue

            run.status = "paused"  # Custom status for paused
            session.add(run)
            session.commit()

            results.append({"run_id": run_id, "status": "paused"})
            successful += 1

            if background_tasks:
                background_tasks.add_task(
                    emit_execution_update,
                    workspace_id,
                    {"type": "bulk_pause", "run_id": run_id},
                )

        except Exception as e:
            results.append(
                {"run_id": run_id, "status": "error", "message": str(e)}
            )
            failed += 1
            session.rollback()

    return BulkOperationResponse(
        operation="pause",
        total_requested=len(run_ids),
        successful=successful,
        failed=failed,
        results=results,
    )


@router.post("/workspace/{workspace_id}/queue/bulk-reorder")
async def bulk_reorder_runs(
    workspace_id: str,
    run_order: list[str],  # New order of run_ids
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> BulkOperationResponse:
    """Reorder multiple runs in the queue."""
    results = []
    successful = 0
    failed = 0

    try:
        # Fetch all runs to reorder
        runs = session.exec(
            select(QueuedRun).where(
                (QueuedRun.workspace_id == workspace_id)
                & (QueuedRun.run_id.in_(run_order))
            )
        ).all()

        run_map = {r.run_id: r for r in runs}

        # Validate all runs exist
        if len(run_map) != len(run_order):
            missing = set(run_order) - set(run_map.keys())
            return BulkOperationResponse(
                operation="reorder",
                total_requested=len(run_order),
                successful=0,
                failed=len(run_order),
                results=[
                    {"run_id": rid, "status": "error", "message": "Run not found"}
                    for rid in missing
                ],
            )

        # Update positions
        for new_position, run_id in enumerate(run_order, 1):
            run = run_map[run_id]
            run.position = new_position
            session.add(run)
            results.append({"run_id": run_id, "status": "reordered", "position": new_position})
            successful += 1

        session.commit()

        if background_tasks:
            background_tasks.add_task(
                emit_execution_update,
                workspace_id,
                {"type": "bulk_reorder", "run_ids": run_order},
            )

    except Exception as e:
        session.rollback()
        return BulkOperationResponse(
            operation="reorder",
            total_requested=len(run_order),
            successful=0,
            failed=len(run_order),
            results=[
                {
                    "run_id": rid,
                    "status": "error",
                    "message": "Reorder failed: " + str(e),
                }
                for rid in run_order
            ],
        )

    return BulkOperationResponse(
        operation="reorder",
        total_requested=len(run_order),
        successful=successful,
        failed=failed,
        results=results,
    )


@router.post("/workspace/{workspace_id}/queue/{run_id}/retry")
async def retry_failed_run(
    workspace_id: str,
    run_id: str,
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
):
    """Retry a failed run with exponential backoff."""
    run = session.exec(
        select(QueuedRun).where(
            (QueuedRun.run_id == run_id)
            & (QueuedRun.workspace_id == workspace_id)
        )
    ).first()

    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.retry_count >= run.max_retries:
        raise HTTPException(
            status_code=400,
            detail=f"Max retries ({run.max_retries}) exceeded",
        )

    # Calculate exponential backoff: 2^retry_count seconds
    backoff_seconds = 2 ** run.retry_count
    run.retry_count += 1
    run.estimated_start_at = datetime.now(timezone.utc) + timedelta(
        seconds=backoff_seconds
    )
    run.failure_reason = ""  # Clear previous failure reason
    run.status = QueuePosition.QUEUED  # Reset to queued

    session.add(run)
    session.commit()

    if background_tasks:
        background_tasks.add_task(
            emit_execution_update,
            workspace_id,
            {
                "type": "retry",
                "run_id": run_id,
                "retry_count": run.retry_count,
                "backoff_seconds": backoff_seconds,
            },
        )

    return {
        "run_id": run_id,
        "retry_count": run.retry_count,
        "max_retries": run.max_retries,
        "backoff_seconds": backoff_seconds,
        "estimated_start_at": run.estimated_start_at.isoformat(),
    }


@router.post("/workspace/{workspace_id}/queue/retry-all-failed")
async def retry_all_failed_runs(
    workspace_id: str,
    max_retries: int | None = None,
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> dict:
    """Retry all failed runs in workspace (with retry count < max_retries)."""
    failed_runs = session.exec(
        select(QueuedRun).where(
            (QueuedRun.workspace_id == workspace_id)
            & (QueuedRun.status == "failed")
        )
    ).all()

    results = []
    for run in failed_runs:
        if run.retry_count >= run.max_retries:
            results.append(
                {
                    "run_id": run.run_id,
                    "status": "skipped",
                    "reason": "Max retries exceeded",
                }
            )
            continue

        backoff_seconds = 2 ** run.retry_count
        run.retry_count += 1
        run.estimated_start_at = datetime.now(timezone.utc) + timedelta(
            seconds=backoff_seconds
        )
        run.failure_reason = ""
        run.status = QueuePosition.QUEUED

        session.add(run)
        results.append(
            {
                "run_id": run.run_id,
                "status": "retrying",
                "retry_count": run.retry_count,
            }
        )

    session.commit()

    if background_tasks:
        background_tasks.add_task(
            emit_execution_update,
            workspace_id,
            {"type": "retry_all_failed", "count": len(results)},
        )

    return {"total": len(failed_runs), "retried": len(results), "results": results}


@router.get("/workspace/{workspace_id}/queue/failed-runs")
async def get_failed_runs(
    workspace_id: str,
    session: Session = Depends(get_session),
) -> list[dict]:
    """Get all failed runs with retry information."""
    failed_runs = session.exec(
        select(QueuedRun).where(
            (QueuedRun.workspace_id == workspace_id)
            & (QueuedRun.status == "failed")
        )
    ).all()

    return [
        {
            "run_id": run.run_id,
            "ticket_id": run.ticket_id,
            "retry_count": run.retry_count,
            "max_retries": run.max_retries,
            "failure_reason": run.failure_reason,
            "last_failed_at": run.last_failed_at.isoformat()
            if run.last_failed_at
            else None,
            "can_retry": run.retry_count < run.max_retries,
        }
        for run in failed_runs
    ]


@router.get("/workspace/{workspace_id}/queue/skip-failed")
async def skip_all_failed_runs(
    workspace_id: str,
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> dict:
    """Skip all failed runs and continue queue."""
    failed_runs = session.exec(
        select(QueuedRun).where(
            (QueuedRun.workspace_id == workspace_id)
            & (QueuedRun.status == "failed")
        )
    ).all()

    skipped_count = 0
    for run in failed_runs:
        run.status = "skipped"
        session.add(run)
        skipped_count += 1

    session.commit()

    if background_tasks and skipped_count > 0:
        background_tasks.add_task(
            emit_execution_update,
            workspace_id,
            {"type": "skip_failed", "count": skipped_count},
        )

    return {"skipped_count": skipped_count}
