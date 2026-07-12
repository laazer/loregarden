"""
WebSocket event emission helpers for parallel execution services.
Provides easy integration points for emitting real-time updates.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from loregarden.websocket import WebSocketServer

logger = logging.getLogger(__name__)

# Global WebSocket server instance
_ws_server: WebSocketServer | None = None


def init_websocket(ws_server: WebSocketServer) -> None:
    """Initialize the global WebSocket server instance."""
    global _ws_server
    _ws_server = ws_server
    logger.info("WebSocket server initialized for event emissions")


def get_ws_server() -> WebSocketServer | None:
    """Get the global WebSocket server instance."""
    return _ws_server


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_execution_update(
    workspace_id: str,
    active_runs: list[dict[str, Any]],
    queued_runs: list[dict[str, Any]],
    stats: dict[str, Any],
) -> None:
    """
    Emit execution status update to workspace subscribers.

    Called when:
    - A new run starts
    - A run completes
    - A run is promoted from queue
    - Queue state changes
    """
    ws = get_ws_server()
    if not ws:
        return

    try:
        ws.broadcast_execution_update(
            workspaceId=workspace_id,
            data={
                "activeRuns": active_runs,
                "queuedRuns": queued_runs,
                "stats": stats,
            },
        )
        logger.debug(
            f"Emitted execution_update to workspace:{workspace_id}",
            extra={"active": len(active_runs), "queued": len(queued_runs)},
        )
    except Exception as e:
        logger.warning(f"Failed to emit execution_update: {e}")


def emit_conflict_detected(
    worktree_id: str,
    run_id: str,
    conflicts: list[dict[str, Any]],
    preview: dict[str, Any],
    severity: str,
) -> None:
    """
    Emit conflict detection event to worktree subscribers.

    Called when:
    - Merge conflicts are detected during dry-run merge
    - Conflict severity assessment completes
    """
    ws = get_ws_server()
    if not ws:
        return

    try:
        ws.broadcast_conflict_detected(
            worktreeId=worktree_id,
            runId=run_id,
            data={
                "conflicts": conflicts,
                "preview": preview,
                "severity": severity,
            },
        )
        logger.debug(
            f"Emitted conflict_detected to worktree:{worktree_id}",
            extra={"severity": severity, "count": len(conflicts)},
        )
    except Exception as e:
        logger.warning(f"Failed to emit conflict_detected: {e}")


def emit_conflict_resolved(
    worktree_id: str,
    run_id: str,
) -> None:
    """
    Emit conflict resolution event to worktree subscribers.

    Called when:
    - Conflicts are manually resolved
    - Auto-merge succeeds and conflicts are cleared
    """
    ws = get_ws_server()
    if not ws:
        return

    try:
        ws.broadcast_conflict_resolved(worktree_id, run_id)
        logger.debug(f"Emitted conflict_resolved to worktree:{worktree_id}")
    except Exception as e:
        logger.warning(f"Failed to emit conflict_resolved: {e}")


def emit_queue_promoted(
    workspace_id: str,
    run_id: str,
    slot_number: int,
) -> None:
    """
    Emit queue promotion event to workspace subscribers.

    Called when:
    - An active run completes and a queued run takes its slot
    - Queue reordering occurs
    """
    ws = get_ws_server()
    if not ws:
        return

    try:
        ws.broadcast_queue_promoted(
            workspaceId=workspace_id,
            data={
                "runId": run_id,
                "slotNumber": slot_number,
                "timestamp": _now_iso(),
            },
        )
        logger.debug(
            f"Emitted queue_promoted to workspace:{workspace_id}",
            extra={"run_id": run_id, "slot": slot_number},
        )
    except Exception as e:
        logger.warning(f"Failed to emit queue_promoted: {e}")


def emit_run_completed(
    workspace_id: str,
    run_id: str,
    status: str,
) -> None:
    """
    Emit run completion event to workspace subscribers.

    Called when:
    - A run finishes (success, failure, or error)
    - Run status becomes final
    """
    ws = get_ws_server()
    if not ws:
        return

    try:
        ws.broadcast_run_completed(
            workspaceId=workspace_id,
            data={
                "runId": run_id,
                "status": status,
                "timestamp": _now_iso(),
            },
        )
        logger.debug(
            f"Emitted run_completed to workspace:{workspace_id}",
            extra={"run_id": run_id, "status": status},
        )
    except Exception as e:
        logger.warning(f"Failed to emit run_completed: {e}")


def emit_error(
    target_room: str,
    message: str,
    code: str,
    context: dict[str, Any] | None = None,
) -> None:
    """
    Emit error event to subscribers.

    Called when:
    - Service-level errors occur during operations
    - Conflict resolution fails
    - Queue management errors happen
    """
    ws = get_ws_server()
    if not ws:
        return

    try:
        ws.broadcast_error(
            targetRoom=target_room,
            data={
                "message": message,
                "code": code,
                "context": context or {},
            },
        )
        logger.debug(f"Emitted error to {target_room}", extra={"code": code, "message": message})
    except Exception as e:
        logger.warning(f"Failed to emit error: {e}")
