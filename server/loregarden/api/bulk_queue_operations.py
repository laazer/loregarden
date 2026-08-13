"""Bulk queue operations: cancel, pause, resume, reorder, retry.

**These address entries by `QueuedRun.id`, not by the agent run behind them.**
They used to key on `QueuedRun.run_id`, which only a shared-queue entry ever
sets — a lane entry names a ticket and its run does not exist until the lane
reaches it. Every entry in a real database is a lane entry, so every endpoint
here matched nothing at all: `bulk-cancel` reported success on rows it never
loaded, and `failed-runs` returned an empty list next to a lane visibly full of
failures. The entry id is what the lane service already moves and removes
things by, so it is what these take.

Status writes go through `QueuePosition`. Ownership of that column really
belongs in a queue-entry service alongside `QueueLaneService`; until that
exists, the rule is that nothing here invents a status the lane cannot read.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from loregarden.api.queue_management import emit_execution_update
from loregarden.db.session import get_session
from loregarden.models.domain import QueuedRun, QueuePosition
from loregarden.services.parallel_queue import WAITING_STATUSES
from sqlmodel import Session, col, select

router = APIRouter(prefix="/api/parallel", tags=["bulk-operations"])


@dataclass
class BulkOperationResponse:
    """Response from bulk operation."""

    operation: str
    total_requested: int
    successful: int
    failed: int
    results: list[dict]  # Per-entry results with status/error


def _entry(session: Session, workspace_id: str, entry_id: str) -> QueuedRun | None:
    return session.exec(
        select(QueuedRun).where(
            (QueuedRun.id == entry_id) & (QueuedRun.workspace_id == workspace_id)
        )
    ).first()


def _bulk_set_status(
    session: Session,
    workspace_id: str,
    entry_ids: list[str],
    *,
    operation: str,
    target: QueuePosition,
    allowed_from: tuple[QueuePosition, ...],
    refusal: str,
    background_tasks: BackgroundTasks | None,
) -> BulkOperationResponse:
    """Move a set of entries into one status, refusing the ones it cannot.

    Cancel, pause and resume are the same operation with different arguments,
    and writing them out three times is how the three drifted apart — only one
    of them checked the entry's current status at all.
    """
    results: list[dict] = []
    successful = 0
    failed = 0

    for entry_id in entry_ids:
        entry = _entry(session, workspace_id, entry_id)
        if not entry:
            results.append({"entry_id": entry_id, "status": "error", "message": "Entry not found"})
            failed += 1
            continue
        if entry.status not in allowed_from:
            results.append(
                {
                    "entry_id": entry_id,
                    "status": "error",
                    "message": f"Entry is {entry.status.value}; {refusal}",
                }
            )
            failed += 1
            continue

        entry.status = target
        session.add(entry)
        results.append({"entry_id": entry_id, "status": operation})
        successful += 1

    if successful:
        session.commit()
        if background_tasks:
            background_tasks.add_task(emit_execution_update)

    return BulkOperationResponse(
        operation=operation,
        total_requested=len(entry_ids),
        successful=successful,
        failed=failed,
        results=results,
    )


@router.post("/workspace/{workspace_id}/queue/bulk-cancel")
async def bulk_cancel_entries(
    workspace_id: str,
    entry_ids: list[str],
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> BulkOperationResponse:
    """Cancel waiting entries.

    Only waiting ones: flipping a row does not stop an agent, and writing
    CANCELLED over live work would have `queue_history` report it cancelled
    while it went on to succeed.
    """
    return _bulk_set_status(
        session,
        workspace_id,
        entry_ids,
        operation="cancelled",
        target=QueuePosition.CANCELLED,
        allowed_from=WAITING_STATUSES,
        refusal="only a waiting entry can be cancelled here",
        background_tasks=background_tasks,
    )


@router.post("/workspace/{workspace_id}/queue/bulk-pause")
async def bulk_pause_entries(
    workspace_id: str,
    entry_ids: list[str],
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> BulkOperationResponse:
    """Hold waiting entries in their lane without letting it start them.

    PAUSED is outside `WAITING_STATUSES`, so a paused entry keeps its lane and
    position but `start_lane_head` will not pick it up. That is only safe
    because `bulk-resume` below puts it back — pausing used to be a one-way
    trip, which is why it selected the terminal STARTED status and so matched
    nothing rather than losing anything.
    """
    return _bulk_set_status(
        session,
        workspace_id,
        entry_ids,
        operation="paused",
        target=QueuePosition.PAUSED,
        allowed_from=WAITING_STATUSES,
        refusal="only a waiting entry can be paused",
        background_tasks=background_tasks,
    )


@router.post("/workspace/{workspace_id}/queue/bulk-resume")
async def bulk_resume_entries(
    workspace_id: str,
    entry_ids: list[str],
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> BulkOperationResponse:
    """Return paused entries to their lane's waiting list.

    The other half of pause, and the reason pause is allowed to exist.
    """
    return _bulk_set_status(
        session,
        workspace_id,
        entry_ids,
        operation="resumed",
        target=QueuePosition.QUEUED,
        allowed_from=(QueuePosition.PAUSED,),
        refusal="only a paused entry can be resumed",
        background_tasks=background_tasks,
    )


@router.post("/workspace/{workspace_id}/queue/bulk-reorder")
async def bulk_reorder_entries(
    workspace_id: str,
    entry_order: list[str],
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> BulkOperationResponse:
    """Set the position of entries, in the order given."""
    entries = session.exec(
        select(QueuedRun).where(
            (QueuedRun.workspace_id == workspace_id) & col(QueuedRun.id).in_(entry_order)
        )
    ).all()
    by_id = {entry.id: entry for entry in entries}

    missing = [entry_id for entry_id in entry_order if entry_id not in by_id]
    if missing:
        return BulkOperationResponse(
            operation="reorder",
            total_requested=len(entry_order),
            successful=0,
            failed=len(entry_order),
            results=[
                {"entry_id": entry_id, "status": "error", "message": "Entry not found"}
                for entry_id in missing
            ],
        )

    results = []
    for position, entry_id in enumerate(entry_order, 1):
        by_id[entry_id].position = position
        session.add(by_id[entry_id])
        results.append({"entry_id": entry_id, "status": "reordered", "position": position})

    session.commit()
    if background_tasks:
        background_tasks.add_task(emit_execution_update)

    return BulkOperationResponse(
        operation="reorder",
        total_requested=len(entry_order),
        successful=len(results),
        failed=0,
        results=results,
    )


def _apply_retry(entry: QueuedRun) -> int:
    """Re-queue a failed entry with exponential backoff. Returns the backoff."""
    backoff_seconds = 2**entry.retry_count
    entry.retry_count += 1
    entry.estimated_start_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
    entry.failure_reason = ""
    entry.status = QueuePosition.QUEUED
    return backoff_seconds


@router.post("/workspace/{workspace_id}/queue/{entry_id}/retry")
async def retry_failed_entry(
    workspace_id: str,
    entry_id: str,
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
):
    """Retry a failed entry with exponential backoff."""
    entry = _entry(session, workspace_id, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.retry_count >= entry.max_retries:
        raise HTTPException(
            status_code=400,
            detail=f"Max retries ({entry.max_retries}) exceeded",
        )

    backoff_seconds = _apply_retry(entry)
    session.add(entry)
    session.commit()

    if background_tasks:
        background_tasks.add_task(emit_execution_update)

    return {
        "entry_id": entry_id,
        "retry_count": entry.retry_count,
        "max_retries": entry.max_retries,
        "backoff_seconds": backoff_seconds,
        "estimated_start_at": entry.estimated_start_at.isoformat(),
    }


def _failed_entries(session: Session, workspace_id: str) -> list[QueuedRun]:
    return list(
        session.exec(
            select(QueuedRun).where(
                (QueuedRun.workspace_id == workspace_id)
                & (QueuedRun.status == QueuePosition.FAILED)
            )
        ).all()
    )


@router.post("/workspace/{workspace_id}/queue/retry-all-failed")
async def retry_all_failed_entries(
    workspace_id: str,
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> dict:
    """Retry every failed entry still under its retry ceiling."""
    failed_entries = _failed_entries(session, workspace_id)

    results = []
    retried = 0
    for entry in failed_entries:
        if entry.retry_count >= entry.max_retries:
            results.append(
                {
                    "entry_id": entry.id,
                    "status": "skipped",
                    "reason": "Max retries exceeded",
                }
            )
            continue
        _apply_retry(entry)
        session.add(entry)
        retried += 1
        results.append(
            {"entry_id": entry.id, "status": "retrying", "retry_count": entry.retry_count}
        )

    session.commit()
    if background_tasks and retried:
        background_tasks.add_task(emit_execution_update)

    # `retried` counts what was actually re-queued; the old field counted every
    # result row, so a workspace where every entry was out of retries reported
    # them all as retried.
    return {"total": len(failed_entries), "retried": retried, "results": results}


@router.get("/workspace/{workspace_id}/queue/failed-entries")
async def get_failed_entries(
    workspace_id: str,
    session: Session = Depends(get_session),
) -> list[dict]:
    """Every failed entry, with its retry budget."""
    return [
        {
            "entry_id": entry.id,
            "run_id": entry.run_id or "",
            "ticket_id": entry.ticket_id,
            "retry_count": entry.retry_count,
            "max_retries": entry.max_retries,
            "failure_reason": entry.failure_reason,
            "last_failed_at": entry.last_failed_at.isoformat() if entry.last_failed_at else None,
            "can_retry": entry.retry_count < entry.max_retries,
        }
        for entry in _failed_entries(session, workspace_id)
    ]


@router.post("/workspace/{workspace_id}/queue/skip-failed")
async def skip_all_failed_entries(
    workspace_id: str,
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> dict:
    """Give up on every failed entry so the lane stops offering them.

    POST, not GET. This writes, and a GET that writes is one prefetch or link
    preview away from skipping a queue nobody asked to skip.
    """
    failed_entries = _failed_entries(session, workspace_id)
    for entry in failed_entries:
        entry.status = QueuePosition.SKIPPED
        session.add(entry)

    if failed_entries:
        session.commit()
        if background_tasks:
            background_tasks.add_task(emit_execution_update)

    return {"skipped_count": len(failed_entries)}
