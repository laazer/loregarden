"""Analytics endpoints for queue performance tracking."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Path, Query
from loregarden.db.session import get_session
from loregarden.models.domain import AgentRun, RunStatus, Ticket
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parallel", tags=["analytics"])


def _build_metrics(session: Session, *, workspace_id: str | None, range: str) -> dict:
    days = {"7d": 7, "30d": 30, "90d": 90}[range]
    now = datetime.now(timezone.utc)
    cutoff_date = now - timedelta(days=days)
    recent_cutoff = now - timedelta(days=7)

    stmt = (
        select(AgentRun, Ticket)
        .join(Ticket, Ticket.id == AgentRun.ticket_id)
        .where(
            (AgentRun.finished_at.isnot(None))
            & (AgentRun.finished_at >= cutoff_date)
        )
    )
    if workspace_id:
        stmt = stmt.where(AgentRun.workspace_id == workspace_id)

    rows = session.exec(stmt).all()

    metrics_by_type: dict[str, dict] = {}

    for run, ticket in rows:
        ticket_type = ticket.work_item_type.value if ticket.work_item_type else "unknown"

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
            (run.finished_at - run.started_at).total_seconds()
            if run.started_at and run.finished_at
            else 0
        )

        bucket = metrics_by_type[ticket_type]
        bucket["count"] += 1
        bucket["durations"].append(duration)

        if run.status == RunStatus.SUCCEEDED:
            bucket["successes"] += 1

        finished_at = run.finished_at
        if finished_at and finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)

        if finished_at and finished_at >= recent_cutoff:
            bucket["last_7_days"]["count"] += 1
            if run.status == RunStatus.SUCCEEDED:
                bucket["last_7_days"]["successes"] += 1

    metrics = []
    for ticket_type, data in metrics_by_type.items():
        durations = data["durations"]
        count = data["count"]
        successes = data["successes"]
        last_7_days = data["last_7_days"]

        avg_duration = sum(durations) / len(durations) if durations else 0
        min_duration = min(durations) if durations else 0
        max_duration = max(durations) if durations else 0
        success_rate = successes / count if count > 0 else 0
        last_7_success_rate = (
            last_7_days["successes"] / last_7_days["count"] if last_7_days["count"] > 0 else 0
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

    metrics.sort(key=lambda m: m["count"], reverse=True)

    return {
        "workspace_id": workspace_id or "",
        "range": range,
        "generated_at": now.isoformat(),
        "metrics": metrics,
    }


@router.get("/analytics")
async def get_global_analytics(
    range: str = Query("7d", pattern="^(7d|30d|90d)$"),
    session: Session = Depends(get_session),
):
    """Historical run metrics across every workspace sharing the slot pool."""
    try:
        return _build_metrics(session, workspace_id=None, range=range)
    except Exception as e:
        logger.error("Error retrieving global analytics: %s", e, exc_info=True)
        return {
            "workspace_id": "",
            "range": range,
            "metrics": [],
            "error": str(e),
        }


@router.get("/workspace/{workspace_id}/analytics")
async def get_analytics(
    workspace_id: str = Path(...),
    range: str = Query("7d", pattern="^(7d|30d|90d)$"),
    session: Session = Depends(get_session),
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
    try:
        body = _build_metrics(session, workspace_id=workspace_id, range=range)
        logger.debug("Analytics retrieved for %s: %d types", workspace_id, len(body["metrics"]))
        return body
    except Exception as e:
        logger.error("Error retrieving analytics: %s", e, exc_info=True)
        return {
            "workspace_id": workspace_id,
            "range": range,
            "metrics": [],
            "error": str(e),
        }
