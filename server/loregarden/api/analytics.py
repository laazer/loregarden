"""Analytics endpoints for queue performance tracking."""

import logging
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Path, Query
from sqlmodel import Session, select, func

from loregarden.core.db import get_session
from loregarden.models.domain import QueuedRun, QueuePosition, AgentRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parallel", tags=["analytics"])


@router.get("/workspace/{workspace_id}/analytics")
async def get_analytics(
    workspace_id: str = Path(...),
    range: str = Query("7d", regex="^(7d|30d|90d)$"),
    session: Session = get_session(),
):
    """
    Get historical run performance metrics.

    Returns per-ticket-type statistics including:
    - Total run count
    - Average duration
    - Min/max durations
    - Success rate
    - Recent (7-day) statistics
    """

    # Determine date range
    days = {"7d": 7, "30d": 30, "90d": 90}[range]
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    recent_cutoff = datetime.utcnow() - timedelta(days=7)

    try:
        # Query completed runs in the workspace
        stmt = select(AgentRun).where(
            (AgentRun.workspace_id == workspace_id)
            & (AgentRun.completed_at >= cutoff_date)
            & (AgentRun.completed_at.isnot(None))
        )

        completed_runs = session.exec(stmt).all()

        # Group by ticket type and calculate metrics
        metrics_by_type = {}

        for run in completed_runs:
            ticket_type = run.ticket_type or "unknown"

            if ticket_type not in metrics_by_type:
                metrics_by_type[ticket_type] = {
                    "ticket_type": ticket_type,
                    "count": 0,
                    "durations": [],
                    "successes": 0,
                    "last_7_days": {
                        "count": 0,
                        "successes": 0,
                    },
                }

            duration = (
                (run.completed_at - run.started_at).total_seconds()
                if run.started_at and run.completed_at
                else 0
            )

            metrics_by_type[ticket_type]["count"] += 1
            metrics_by_type[ticket_type]["durations"].append(duration)

            # Track success (status == 'completed')
            if run.status == "completed":
                metrics_by_type[ticket_type]["successes"] += 1

            # Track last 7 days
            if run.completed_at >= recent_cutoff:
                metrics_by_type[ticket_type]["last_7_days"]["count"] += 1
                if run.status == "completed":
                    metrics_by_type[ticket_type]["last_7_days"]["successes"] += 1

        # Calculate statistics
        metrics = []
        for ticket_type, data in metrics_by_type.items():
            durations = data["durations"]
            count = data["count"]
            successes = data["successes"]
            last_7_days = data["last_7_days"]

            avg_duration = (
                sum(durations) / len(durations) if durations else 0
            )
            min_duration = min(durations) if durations else 0
            max_duration = max(durations) if durations else 0
            success_rate = successes / count if count > 0 else 0

            last_7_success_rate = (
                last_7_days["successes"] / last_7_days["count"]
                if last_7_days["count"] > 0
                else 0
            )

            metrics.append(
                {
                    "ticket_type": ticket_type,
                    "count": count,
                    "avg_duration_seconds": round(avg_duration, 2),
                    "min_duration_seconds": round(min_duration, 2),
                    "max_duration_seconds": round(max_duration, 2),
                    "success_rate": round(success_rate, 4),
                    "last_7_days_count": last_7_days["count"],
                    "last_7_days_success_rate": round(last_7_success_rate, 4),
                }
            )

        # Sort by count (most frequent first)
        metrics.sort(key=lambda m: m["count"], reverse=True)

        logger.debug(f"Analytics retrieved for {workspace_id}: {len(metrics)} types")

        return {
            "workspace_id": workspace_id,
            "range": range,
            "generated_at": datetime.utcnow().isoformat(),
            "metrics": metrics,
        }

    except Exception as e:
        logger.error(f"Error retrieving analytics: {e}", exc_info=True)
        return {
            "workspace_id": workspace_id,
            "range": range,
            "metrics": [],
            "error": str(e),
        }
