"""How long runs actually take, and what that implies for clearing the queue.

The dashboard used to draw every progress bar against a hardcoded 300-second
run and compute "estimated clear" as 300 plus 300 per queued run. Neither
number came from anywhere: a two-second run and a twenty-minute run drew the
same bar.

Here the denominator is the median of what that agent has actually taken in
this workspace, and a workspace with no completed runs reports ``None`` — the
dashboard says "unknown" rather than inventing a figure. That is the point of
the module; a constant default would put the fabrication back.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from loregarden.models.domain import AgentRun, RunStatus
from sqlmodel import Session, select

#: How far back to look for completed runs. Matches the widest window the
#: analytics endpoint offers, so both read the same history.
DEFAULT_LOOKBACK_DAYS = 90

#: Key holding the workspace-wide median, used for an agent that has never
#: finished a run here.
FALLBACK_KEY = "*"


def _duration_seconds(run: AgentRun) -> float | None:
    if not run.started_at or not run.finished_at:
        return None
    return (run.finished_at - run.started_at).total_seconds()


def median_duration_by_agent(
    session: Session,
    workspace_id: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, float]:
    """Median seconds per successful run, keyed by agent slug.

    Only successes count. A run that failed after ten seconds is a real
    duration but not a useful prediction of how long the same work takes when
    it goes through, and including failures makes every estimate optimistic
    exactly on the agents that fail most.

    Returns an empty dict when there is no history — callers must treat that as
    "no estimate available", not as zero.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    stmt = select(AgentRun).where(
        (AgentRun.workspace_id == workspace_id)
        & (AgentRun.status == RunStatus.SUCCEEDED)
        & (AgentRun.started_at.isnot(None))
        & (AgentRun.finished_at.isnot(None))
        & (AgentRun.finished_at >= cutoff)
    )

    samples: dict[str, list[float]] = defaultdict(list)
    for run in session.exec(stmt).all():
        seconds = _duration_seconds(run)
        if seconds is None or seconds <= 0:
            continue
        samples[run.agent_id].append(seconds)
        samples[FALLBACK_KEY].append(seconds)

    return {key: median(values) for key, values in samples.items()}


def estimate_for(medians: dict[str, float], agent_id: str | None) -> float | None:
    """This agent's median, the workspace's median, or nothing."""
    if not medians:
        return None
    if agent_id and agent_id in medians:
        return medians[agent_id]
    return medians.get(FALLBACK_KEY)


def project_clear_time(
    active_runs: list[dict[str, Any]],
    queued_runs: list[dict[str, Any]],
    medians: dict[str, float],
    max_concurrent: int,
) -> float | None:
    """Seconds until the last currently-known run finishes.

    Simulates the scheduler rather than summing: each slot carries the time
    left on whatever occupies it, and every queued run lands on the slot that
    frees up first. With three slots and three queued runs that is one run's
    wait, not three — which is where summing went wrong.
    """
    if not medians:
        return None
    if not active_runs and not queued_runs:
        return 0.0

    free_at: list[float] = []
    for run in active_runs:
        estimate = estimate_for(medians, run.get("agent_id")) or 0.0
        elapsed = float(run.get("elapsed_seconds") or 0)
        # A run past its median is not finished, it is overdue. Zero means
        # "expected imminently", which is the most honest thing the median can
        # say about it.
        free_at.append(max(0.0, estimate - elapsed))

    free_at.extend([0.0] * max(0, max_concurrent - len(free_at)))

    for run in queued_runs:
        soonest = min(range(len(free_at)), key=free_at.__getitem__)
        free_at[soonest] += estimate_for(medians, run.get("agent_id")) or 0.0

    return max(free_at)
