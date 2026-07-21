"""The parallel-execution snapshot, in one place.

Both the REST status endpoint and the queue websocket answer the same
question, and a client that switches between them must not see a different
shape depending on which one replied — so neither builds the payload itself.
"""

from __future__ import annotations

from typing import Any

from loregarden.services.parallel_queue import ParallelQueueService
from sqlmodel import Session

#: Slots per workspace. Matches the default the REST endpoint has always used.
DEFAULT_MAX_CONCURRENT = 3


async def build_queue_status(session: Session, workspace_id: str) -> dict[str, Any]:
    """Active runs, queued runs and slot statistics for one workspace."""
    queue_service = ParallelQueueService(session, max_concurrent=DEFAULT_MAX_CONCURRENT)

    active_runs = await queue_service.get_active_runs(workspace_id)
    queued_runs = await queue_service.get_queued_runs(workspace_id)
    stats = queue_service.get_queue_stats(workspace_id)

    return {
        "active_runs": active_runs,
        "queued_runs": queued_runs,
        "available_slots": stats.get("available_slots", 0),
        "total_slots": stats.get("max_concurrent", DEFAULT_MAX_CONCURRENT),
        "queue_length": len(queued_runs),
        "stats": stats,
    }
