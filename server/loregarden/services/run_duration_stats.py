"""How long runs actually take, and what that implies for clearing the queue.

The dashboard used to draw every progress bar against a hardcoded 300-second
run and compute "estimated clear" as 300 plus 300 per queued run. Neither
number came from anywhere: a two-second run and a twenty-minute run drew the
same bar.

Here the denominator is the median of what that agent has actually taken in
this workspace, and a workspace with no completed runs reports ``None`` — the
dashboard says "unknown" rather than inventing a figure. That is the point of
the module; a constant default would put the fabrication back.

A per-agent median alone still under-reads a pipeline, for two reasons this
module measures rather than guesses:

* **Stages are not agents.** The same agent costs differently at `implement`
  and at `triage`, and a pending stage names both — so a stage's own history
  wins over its agent's when there is any.
* **Stages re-run.** Rework routes send a stage back, and history says how
  often: `attempts_per_stage` is agent runs divided by distinct
  (orchestration, stage) pairs. Costing a pending stage at one attempt is why
  a whole-ticket estimate used to read low by nearly half.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
)
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
    workspace_id: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, float]:
    """Median seconds per successful run, keyed by agent slug.

    `workspace_id=None` draws on every workspace's history, which is what the
    queue wants: one shared slot pool means one shared waiting line, and the
    estimate for a queued run cannot depend on which workspace asks.

    Only successes count. A run that failed after ten seconds is a real
    duration but not a useful prediction of how long the same work takes when
    it goes through, and including failures makes every estimate optimistic
    exactly on the agents that fail most.

    Returns an empty dict when there is no history — callers must treat that as
    "no estimate available", not as zero.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    conditions = [
        AgentRun.status == RunStatus.SUCCEEDED,
        AgentRun.started_at.isnot(None),
        AgentRun.finished_at.isnot(None),
        AgentRun.finished_at >= cutoff,
    ]
    if workspace_id is not None:
        conditions.append(AgentRun.workspace_id == workspace_id)

    stmt = select(AgentRun).where(*conditions)

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


#: Stage keys that name the same stage in different templates. Aggregation
#: groups by the canonical spelling on the left; nothing else in the system is
#: touched, so routing, templates and instances keep their own keys
#: (lg-workflow-integrity-558).
#:
#: Verified before merging: no template declares both spellings of a pair, and
#: none of the 172 recorded orchestrations ran both — so folding them cannot
#: merge two genuinely distinct stages. A pair that ever does become distinct
#: must come out of this table.
#:
#: One table rather than comparisons at each call site, because a mapping that
#: lives in three places is a mapping that disagrees with itself.
CANONICAL_STAGE_KEYS: dict[str, str] = {
    "implementation": "implement",
    "test_design": "test-design",
    "test_break": "test-break",
    "planning": "plan",
    "specification": "spec",
}


def canonical_stage_key(stage_key: str) -> str:
    """The spelling this stage's history is aggregated under.

    Unknown keys pass through unchanged: a stage nobody has forked is already
    canonical, and inventing a normalisation rule for it would silently merge
    stages that only look similar.
    """
    return CANONICAL_STAGE_KEYS.get(stage_key, stage_key)


#: Below this many observations a per-key median is noise, and a single
#: outlying run would swing every estimate built on it. Such keys fall through
#: to the next-broadest sample rather than being trusted.
MIN_SAMPLES = 3


@dataclass(frozen=True)
class DurationStats:
    """What history says a stage costs, and how often a stage runs twice."""

    by_agent: dict[str, float] = field(default_factory=dict)
    by_stage: dict[str, float] = field(default_factory=dict)
    #: Agent runs per distinct (orchestration, stage) pair — 1.0 when nothing
    #: has ever re-run, never below it.
    attempts_per_stage: float = 1.0

    def has_history(self) -> bool:
        return bool(self.by_agent)

    def stage_seconds(self, stage_key: str | None, agent_id: str | None) -> float | None:
        """One attempt at this stage, most specific evidence first.

        A stage's own history beats its agent's: the same implementer costs
        one thing at `implement` and another at `fix_review_findings`.
        """
        if stage_key:
            # Canonicalised on the way in too: a caller holding the raw key
            # from a ticket or template would otherwise miss the median stored
            # under its canonical twin.
            median_seconds = self.by_stage.get(canonical_stage_key(stage_key))
            if median_seconds is not None:
                return median_seconds
        return estimate_for(self.by_agent, agent_id)


def load_duration_stats(
    session: Session,
    workspace_id: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> DurationStats:
    """Medians by agent and by stage, plus the observed rework multiplier.

    One pass over the same window `median_duration_by_agent` reads, so the two
    can never disagree about what history contains.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    conditions = [
        AgentRun.started_at.isnot(None),
        AgentRun.finished_at.isnot(None),
        AgentRun.finished_at >= cutoff,
    ]
    if workspace_id is not None:
        conditions.append(AgentRun.workspace_id == workspace_id)

    by_agent: dict[str, list[float]] = defaultdict(list)
    by_stage: dict[str, list[float]] = defaultdict(list)
    attempts = 0
    stage_pairs: set[tuple[str, str]] = set()

    for run in session.exec(select(AgentRun).where(*conditions)).all():
        if run.orchestration_run_id and run.stage_key:
            # Every attempt counts here, including the failures: a stage that
            # failed and ran again cost the pipeline both runs.
            attempts += 1
            stage_pairs.add((run.orchestration_run_id, canonical_stage_key(run.stage_key)))

        if run.status != RunStatus.SUCCEEDED:
            continue
        seconds = _duration_seconds(run)
        if seconds is None or seconds <= 0:
            continue
        by_agent[run.agent_id].append(seconds)
        by_agent[FALLBACK_KEY].append(seconds)
        if run.stage_key:
            # Grouped by canonical spelling so `implement` and `implementation`
            # contribute to one median. Before this, MIN_SAMPLES dropped the
            # smaller variant entirely and computed the larger one from a
            # fraction of the real sample.
            by_stage[canonical_stage_key(run.stage_key)].append(seconds)

    return DurationStats(
        by_agent={
            key: median(values)
            for key, values in by_agent.items()
            # The workspace-wide fallback is exempt: it is the last resort, and
            # withholding it on a young workspace turns every estimate into
            # "unknown" when a rough figure was available.
            if key == FALLBACK_KEY or len(values) >= MIN_SAMPLES
        },
        by_stage={
            key: median(values) for key, values in by_stage.items() if len(values) >= MIN_SAMPLES
        },
        attempts_per_stage=(max(1.0, attempts / len(stage_pairs)) if stage_pairs else 1.0),
    )


def median_orchestration_seconds(
    session: Session,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> float | None:
    """Median wall-clock of a completed orchestration, or None.

    The last-resort figure for a ticket whose workflow cannot be resolved at
    all. Summing stages is the better estimate everywhere it is available,
    because it knows which stages are already behind the ticket.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = session.exec(
        select(OrchestrationRun).where(
            OrchestrationRun.status == OrchestrationRunStatus.SUCCEEDED,
            OrchestrationRun.started_at.isnot(None),
            OrchestrationRun.finished_at.isnot(None),
            OrchestrationRun.finished_at >= cutoff,
        )
    ).all()
    samples = [
        (row.finished_at - row.started_at).total_seconds()
        for row in rows
        if row.started_at and row.finished_at and row.finished_at > row.started_at
    ]
    return median(samples) if len(samples) >= MIN_SAMPLES else None


#: Set by the caller on a run dict that already knows its own cost — a lane
#: entry's cost is a whole ticket's remaining pipeline, which no per-agent
#: median can express. Absent, the projection falls back to the medians.
REMAINING_KEY = "estimated_remaining_seconds"
DURATION_KEY = "estimated_duration_seconds"


def _active_remaining(run: dict[str, Any], medians: dict[str, float]) -> float:
    """Time left on something already executing."""
    precomputed = run.get(REMAINING_KEY)
    if precomputed is not None:
        return max(0.0, float(precomputed))
    estimate = estimate_for(medians, run.get("agent_id")) or 0.0
    elapsed = float(run.get("elapsed_seconds") or 0)
    # A run past its median is not finished, it is overdue. Zero means
    # "expected imminently", which is the most honest thing the median can say
    # about it.
    return max(0.0, estimate - elapsed)


def _queued_cost(run: dict[str, Any], medians: dict[str, float]) -> float:
    precomputed = run.get(REMAINING_KEY)
    if precomputed is None:
        precomputed = run.get(DURATION_KEY)
    if precomputed is not None:
        return max(0.0, float(precomputed))
    return estimate_for(medians, run.get("agent_id")) or 0.0


def project_lane_waits(
    active_runs: list[dict[str, Any]],
    queued_runs: list[dict[str, Any]],
    medians: dict[str, float],
    max_concurrent: int,
) -> list[float]:
    """Seconds until each queued entry *starts*, positionally aligned to it.

    Entries carrying a ``slot_number`` are pinned to that lane — a lane is a
    serial pipeline, and position 3 in lane 1 cannot start in lane 2 because
    lane 2 drained first. Projecting those against the whole pool is what made
    a deep lane read as though it were about to start.
    """
    lane_free: dict[Any, float] = {}
    for run in active_runs:
        lane = run.get("slot_number")
        remaining = _active_remaining(run, medians)
        # Two occupants of one lane cannot happen, but a defensive max keeps a
        # reconciliation race from reporting the shorter of the two.
        lane_free[lane] = max(lane_free.get(lane, 0.0), remaining)

    waits: list[float] = []
    unpinned: list[float] = [
        lane_free.get(number, 0.0) for number in range(1, max(1, max_concurrent) + 1)
    ]

    for run in queued_runs:
        lane = run.get("slot_number")
        if lane is None:
            # No lane of its own: the free-for-all pool, first slot to open.
            index = min(range(len(unpinned)), key=unpinned.__getitem__)
            waits.append(unpinned[index])
            unpinned[index] += _queued_cost(run, medians)
            continue
        wait = lane_free.get(lane, 0.0)
        waits.append(wait)
        lane_free[lane] = wait + _queued_cost(run, medians)

    return waits


def project_clear_time(
    active_runs: list[dict[str, Any]],
    queued_runs: list[dict[str, Any]],
    medians: dict[str, float],
    max_concurrent: int,
) -> float | None:
    """Seconds until the last currently-known run finishes.

    Simulates the scheduler rather than summing: each lane carries the time
    left on whatever occupies it plus everything queued behind it, and an
    unpinned run lands on whichever slot frees up first. With three slots and
    three unpinned runs that is one run's wait, not three — which is where
    summing went wrong.
    """
    precomputed = any(run.get(REMAINING_KEY) is not None for run in active_runs + queued_runs)
    if not medians and not precomputed:
        return None
    if not active_runs and not queued_runs:
        return 0.0

    waits = project_lane_waits(active_runs, queued_runs, medians, max_concurrent)

    finishes = [_active_remaining(run, medians) for run in active_runs]
    finishes += [
        wait + _queued_cost(run, medians) for run, wait in zip(queued_runs, waits, strict=True)
    ]

    return max(finishes, default=0.0)
