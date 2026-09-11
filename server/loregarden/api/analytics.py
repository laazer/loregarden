"""Analytics endpoints for queue performance tracking."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from loregarden.db.session import get_session
from loregarden.models.domain import AgentRun, RunStatus, Ticket
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.rework_feedback import MAX_REWORK_REROUTES
from loregarden.services.stage_attempt_stats import (
    DEFAULT_LOOKBACK_DAYS,
    RetryCounter,
    cap_pressure,
    load_stage_attempt_stats,
)
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
        .where((AgentRun.finished_at.isnot(None)) & (AgentRun.finished_at >= cutoff_date))
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
def get_global_analytics(
    range: str = Query("7d", pattern="^(7d|30d|90d)$"),
    session: Session = Depends(get_session),
):
    """Historical run metrics across every workspace sharing the slot pool."""
    try:
        return _build_metrics(session, workspace_id=None, range=range)
    except Exception as e:  # noqa: BLE001 - endpoint boundary
        # A 200 carrying `metrics: []` is shaped exactly like a real empty
        # result, so a broken query rendered as "no data yet". Fail loudly and
        # let the client's error path show what happened.
        logger.exception("Error retrieving global analytics")
        raise HTTPException(status_code=500, detail=f"Could not retrieve analytics: {e}") from e


@router.get("/workspace/{workspace_id}/analytics")
def get_analytics(
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
    except Exception as e:  # noqa: BLE001 - endpoint boundary
        # See get_global_analytics: an empty metrics list is a valid answer, so
        # it must never double as the error channel.
        logger.exception("Error retrieving analytics for %s", workspace_id)
        raise HTTPException(status_code=500, detail=f"Could not retrieve analytics: {e}") from e


@router.get("/stage-attempts")
def get_stage_attempt_stats(
    workspace_id: str = Query("", description="Limit to one workspace; empty means all"),
    lookback_days: int = Query(DEFAULT_LOOKBACK_DAYS, ge=1, le=365),
    session: Session = Depends(get_session),
):
    """How many attempts each stage needs, and whether the retry caps bound it.

    Exists because four separate ceilings on re-running a stage were set by
    judgement and nothing measured them. Returns the per-stage attempt profiles
    alongside a `caps` block comparing each configured ceiling against the worst
    case actually observed — see `services.stage_attempt_stats` for what the
    `pass`-verdict oracle does and does not tell you.
    """
    try:
        stats = load_stage_attempt_stats(
            session,
            workspace_id=workspace_id or None,
            lookback_days=lookback_days,
        )
    except Exception as e:  # noqa: BLE001 - endpoint boundary
        # Same reasoning as the analytics endpoints above: a 200 carrying empty
        # profiles is shaped exactly like "nothing has run yet", so a broken
        # query would read as a quiet, plausible answer.
        logger.exception("Error computing stage attempt stats")
        raise HTTPException(status_code=500, detail=f"Could not compute attempts: {e}") from e

    # The default profile's numbers, not a resolved workspace's: this endpoint
    # spans workspaces by default, and a per-workspace override is visible in
    # that workspace's own profile. Each cap is read against its own counter —
    # see `RetryCounter` for why comparing them all to one metric was wrong.
    profile = OrchestrationProfile(slug="default")
    measured = [
        (
            "retry_budget.max_attempts_per_stage",
            profile.retry_budget.max_attempts_per_stage,
            RetryCounter.DISPATCH,
        ),
        (
            "gates.autofix_max_agent_attempts",
            profile.gates.autofix_max_agent_attempts,
            RetryCounter.GATE_FIX,
        ),
        ("rework_feedback.MAX_REWORK_REROUTES", MAX_REWORK_REROUTES, RetryCounter.REWORK),
        (
            "retry_budget.max_transient_retries",
            profile.retry_budget.max_transient_retries,
            RetryCounter.TRANSIENT,
        ),
    ]
    caps = [
        cap_pressure(
            session,
            name=name,
            cap=cap,
            counter=counter,
            workspace_id=workspace_id or None,
            lookback_days=lookback_days,
        )
        for name, cap, counter in measured
    ]
    return {"stats": stats.model_dump(mode="json"), "caps": [cap.model_dump() for cap in caps]}
