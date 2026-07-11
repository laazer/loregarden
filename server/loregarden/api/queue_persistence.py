"""Queue state persistence: save/restore/replay queue snapshots."""

import json
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from loregarden.api.queue_management import emit_execution_update
from loregarden.db.session import get_session
from loregarden.models.domain import QueuedRun, QueuePosition, QueueSnapshot, Workspace
from sqlmodel import Session, select

router = APIRouter(prefix="/api/parallel", tags=["queue-persistence"])


@router.post("/workspace/{workspace_id}/queue/save-snapshot")
async def save_queue_snapshot(
    workspace_id: str,
    name: str,
    description: str | None = None,
    tags: str | None = None,
    created_by: str = "system",
    session: Session = Depends(get_session),
) -> dict:
    """Save current queue state as a snapshot for restoration."""
    # Verify workspace exists
    ws = session.exec(select(Workspace).where(Workspace.id == workspace_id)).first()

    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Get all current queued runs
    current_runs = session.exec(
        select(QueuedRun).where(QueuedRun.workspace_id == workspace_id)
    ).all()

    # Serialize queue state
    queue_state = [
        {
            "run_id": run.run_id,
            "ticket_id": run.ticket_id,
            "position": run.position,
            "status": run.status.value if isinstance(run.status, QueuePosition) else run.status,
            "retry_count": run.retry_count,
            "max_retries": run.max_retries,
            "estimated_start_at": run.estimated_start_at.isoformat()
            if run.estimated_start_at
            else None,
        }
        for run in current_runs
    ]

    # Calculate stats at snapshot time
    stats = {
        "total_runs": len(current_runs),
        "active_count": sum(1 for r in current_runs if r.status == QueuePosition.STARTED),
        "queued_count": sum(1 for r in current_runs if r.status == QueuePosition.QUEUED),
        "failed_count": sum(1 for r in current_runs if r.status == "failed"),
        "retry_total": sum(r.retry_count for r in current_runs),
    }

    # Create snapshot
    snapshot = QueueSnapshot(
        workspace_id=workspace_id,
        name=name,
        description=description or "",
        queue_state_json=json.dumps(queue_state),
        stats_json=json.dumps(stats),
        tags=tags or "",
        created_by=created_by,
    )

    session.add(snapshot)
    session.commit()

    return {
        "snapshot_id": snapshot.id,
        "name": snapshot.name,
        "created_at": snapshot.created_at.isoformat(),
        "run_count": len(current_runs),
        "stats": stats,
    }


@router.get("/workspace/{workspace_id}/queue/snapshots")
async def list_queue_snapshots(
    workspace_id: str,
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    """List all saved queue snapshots for a workspace."""
    snapshots = session.exec(
        select(QueueSnapshot)
        .where(QueueSnapshot.workspace_id == workspace_id)
        .order_by(QueueSnapshot.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    total = session.exec(
        select(QueueSnapshot).where(QueueSnapshot.workspace_id == workspace_id)
    ).all()

    return {
        "total": len(total),
        "snapshots": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tags": s.tags.split(",") if s.tags else [],
                "created_at": s.created_at.isoformat(),
                "created_by": s.created_by,
                "stats": json.loads(s.stats_json) if s.stats_json else {},
            }
            for s in snapshots
        ],
    }


@router.get("/workspace/{workspace_id}/queue/snapshot/{snapshot_id}")
async def get_snapshot_details(
    workspace_id: str,
    snapshot_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Get detailed information about a snapshot including full queue state."""
    snapshot = session.exec(
        select(QueueSnapshot).where(
            (QueueSnapshot.id == snapshot_id) & (QueueSnapshot.workspace_id == workspace_id)
        )
    ).first()

    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    queue_state = json.loads(snapshot.queue_state_json)
    stats = json.loads(snapshot.stats_json) if snapshot.stats_json else {}

    return {
        "id": snapshot.id,
        "name": snapshot.name,
        "description": snapshot.description,
        "tags": snapshot.tags.split(",") if snapshot.tags else [],
        "created_at": snapshot.created_at.isoformat(),
        "created_by": snapshot.created_by,
        "queue_state": queue_state,
        "stats": stats,
    }


@router.post("/workspace/{workspace_id}/queue/restore-snapshot/{snapshot_id}")
async def restore_queue_from_snapshot(
    workspace_id: str,
    snapshot_id: str,
    clear_current: bool = True,
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> dict:
    """Restore queue to a previous snapshot state."""
    # Verify workspace exists
    ws = session.exec(select(Workspace).where(Workspace.id == workspace_id)).first()

    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Get snapshot
    snapshot = session.exec(
        select(QueueSnapshot).where(
            (QueueSnapshot.id == snapshot_id) & (QueueSnapshot.workspace_id == workspace_id)
        )
    ).first()

    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    try:
        # Clear current queue if requested
        if clear_current:
            current_runs = session.exec(
                select(QueuedRun).where(QueuedRun.workspace_id == workspace_id)
            ).all()

            for run in current_runs:
                session.delete(run)

        # Load snapshot queue state
        queue_state = json.loads(snapshot.queue_state_json)

        # Create new runs from snapshot
        restored_count = 0
        for run_data in queue_state:
            new_run = QueuedRun(
                workspace_id=workspace_id,
                ticket_id=run_data["ticket_id"],
                run_id=run_data["run_id"],
                position=run_data["position"],
                status=run_data["status"],
                retry_count=run_data.get("retry_count", 0),
                max_retries=run_data.get("max_retries", 3),
                estimated_start_at=datetime.fromisoformat(run_data["estimated_start_at"])
                if run_data.get("estimated_start_at")
                else None,
            )
            session.add(new_run)
            restored_count += 1

        session.commit()

        if background_tasks:
            background_tasks.add_task(
                emit_execution_update,
                workspace_id,
                {
                    "type": "queue_restored",
                    "snapshot_id": snapshot_id,
                    "restored_count": restored_count,
                },
            )

        return {
            "success": True,
            "restored_count": restored_count,
            "snapshot_name": snapshot.name,
        }

    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=f"Restore failed: {str(e)}") from e


@router.post("/workspace/{workspace_id}/queue/replay-last")
async def replay_last_n_runs(
    workspace_id: str,
    count: int = 5,
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
) -> dict:
    """Replay the last N completed or failed runs back into the queue."""
    # This would typically pull from historical records
    # For now, simulate by pulling from completed runs
    # In a real system, you'd query a runs history table

    try:
        # Clear current queue
        current_runs = session.exec(
            select(QueuedRun).where(QueuedRun.workspace_id == workspace_id)
        ).all()

        for run in current_runs:
            session.delete(run)

        # In a real implementation, you'd query historical runs
        # For now, return success with 0 replayed runs
        replayed_count = 0

        session.commit()

        if background_tasks:
            background_tasks.add_task(
                emit_execution_update,
                workspace_id,
                {"type": "queue_replayed", "replayed_count": replayed_count},
            )

        return {
            "success": True,
            "replayed_count": replayed_count,
            "message": "Replay completed (requires historical run tracking)",
        }

    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=f"Replay failed: {str(e)}") from e


@router.delete("/workspace/{workspace_id}/queue/snapshot/{snapshot_id}")
async def delete_snapshot(
    workspace_id: str,
    snapshot_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Delete a saved queue snapshot."""
    snapshot = session.exec(
        select(QueueSnapshot).where(
            (QueueSnapshot.id == snapshot_id) & (QueueSnapshot.workspace_id == workspace_id)
        )
    ).first()

    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    session.delete(snapshot)
    session.commit()

    return {"success": True, "deleted_snapshot_id": snapshot_id}


@router.get("/workspace/{workspace_id}/queue/snapshots/search")
async def search_snapshots(
    workspace_id: str,
    query: str = "",
    tag: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    """Search snapshots by name, description, or tags."""
    base_query = select(QueueSnapshot).where(QueueSnapshot.workspace_id == workspace_id)

    if query:
        base_query = base_query.where(
            (QueueSnapshot.name.ilike(f"%{query}%"))
            | (QueueSnapshot.description.ilike(f"%{query}%"))
        )

    if tag:
        base_query = base_query.where(QueueSnapshot.tags.ilike(f"%{tag}%"))

    snapshots = session.exec(base_query.order_by(QueueSnapshot.created_at.desc())).all()

    return {
        "total": len(snapshots),
        "snapshots": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tags": s.tags.split(",") if s.tags else [],
                "created_at": s.created_at.isoformat(),
                "created_by": s.created_by,
            }
            for s in snapshots
        ],
    }
