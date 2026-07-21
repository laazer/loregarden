"""Named domain events, published to whatever sockets are listening.

These are *notifications*, not payloads. A subscriber re-reads the state it
cares about when one arrives, rather than trusting a snapshot assembled by
whichever service happened to emit — two sources of truth for one screen
disagree eventually, and the emitter's view is the one computed on a session
that may already have moved on.

That is why `emit_execution_update` takes only a workspace id. It used to
demand the active runs, queued runs and stats, so every call site ran three
queries to build an argument that was then dropped on the floor: nothing ever
instantiated the Flask-SocketIO server these called into, so `get_ws_server()`
returned None and every emit was a no-op. Two call sites had been passing the
wrong arity for long enough to prove nobody noticed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from loregarden.services.event_hub import event_hub

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_topic(workspace_id: str) -> str:
    return f"workspace:{workspace_id}"


def _worktree_topic(worktree_id: str) -> str:
    return f"worktree:{worktree_id}"


def emit_execution_update(workspace_id: str) -> None:
    """The queue for this workspace changed.

    Called when a run starts, completes, is promoted out of the queue, or the
    queue is otherwise reordered.
    """
    event_hub.publish(
        _workspace_topic(workspace_id),
        {"type": "execution_update", "timestamp": _now_iso()},
    )


def emit_conflict_detected(
    worktree_id: str,
    run_id: str,
    conflicts: list[dict[str, Any]],
    preview: dict[str, Any],
    severity: str,
) -> None:
    """A dry-run merge found conflicts in this worktree."""
    event_hub.publish(
        _worktree_topic(worktree_id),
        {
            "type": "conflict_detected",
            "worktreeId": worktree_id,
            "runId": run_id,
            "timestamp": _now_iso(),
            "data": {"conflicts": conflicts, "preview": preview, "severity": severity},
        },
    )


def emit_conflict_resolved(worktree_id: str, run_id: str) -> None:
    """Conflicts in this worktree are gone — resolved by hand or auto-merged."""
    event_hub.publish(
        _worktree_topic(worktree_id),
        {
            "type": "conflict_resolved",
            "worktreeId": worktree_id,
            "runId": run_id,
            "timestamp": _now_iso(),
        },
    )


def emit_queue_promoted(workspace_id: str, run_id: str, slot_number: int) -> None:
    """A queued run took a freed slot."""
    event_hub.publish(
        _workspace_topic(workspace_id),
        {
            "type": "queue_promoted",
            "timestamp": _now_iso(),
            "data": {"runId": run_id, "slotNumber": slot_number},
        },
    )


def emit_run_completed(workspace_id: str, run_id: str, status: str) -> None:
    """A run reached a final state, whatever that state is."""
    event_hub.publish(
        _workspace_topic(workspace_id),
        {
            "type": "run_completed",
            "timestamp": _now_iso(),
            "data": {"runId": run_id, "status": status},
        },
    )


def emit_error(
    target_room: str,
    message: str,
    code: str,
    context: dict[str, Any] | None = None,
) -> None:
    """A service-level failure, addressed to an already-formed topic."""
    event_hub.publish(
        target_room,
        {
            "type": "error",
            "timestamp": _now_iso(),
            "data": {"message": message, "code": code, "context": context or {}},
        },
    )
