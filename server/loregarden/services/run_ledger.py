"""What actually happened to a ticket, in the order it happened.

The runs a ticket accumulates were listed flat — a row per run, showing the
command. That tells you work occurred but not the shape of it: which stage each
run belonged to, whether a stage was attempted more than once, whether the
pipeline ever went backwards.

Those are the things worth seeing. A gate that auto-fixed twice, a verify that
refused and sent the work back to implement, three planners running at once —
all of it was already in `agent_runs`, and none of it was legible.

A *visit* is one contiguous stretch of runs on the same stage. Consecutive runs
of a stage are the retries within one visit; the same stage appearing again
after another stage is a second visit, which is exactly the signal that the
workflow looped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from loregarden.models.domain import AgentRun, RunStatus

#: Statuses that mean a run is still going, so its visit has no end yet.
_OPEN_STATUSES = frozenset({RunStatus.RUNNING, RunStatus.QUEUED})


@dataclass
class LedgerAttempt:
    """One agent run — a single lane of a single visit."""

    run_id: str
    run_code: str
    agent_id: str
    skill_name: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def duration_seconds(self) -> float | None:
        if not self.started_at or not self.finished_at:
            return None
        return max(0.0, (self.finished_at - self.started_at).total_seconds())


@dataclass
class LedgerVisit:
    """One contiguous stretch of work on a stage."""

    stage_key: str
    attempts: list[LedgerAttempt] = field(default_factory=list)
    #: 1 for the first time the ticket reached this stage, 2 for the next, and
    #: so on. Anything above 1 means the pipeline came back.
    visit_number: int = 1

    @property
    def is_parallel(self) -> bool:
        """Whether this visit fanned out rather than retried.

        Distinguished by *distinct lanes*, not attempt count: three planners
        under different lenses is a fan-out, while the same agent and skill
        three times over is one lane retrying.
        """
        lanes = {(a.agent_id, a.skill_name) for a in self.attempts}
        return len(lanes) > 1

    @property
    def status(self) -> str:
        """The visit's outcome: still open, else the last attempt's status."""
        if any(a.status in {s.value for s in _OPEN_STATUSES} for a in self.attempts):
            return "running"
        return self.attempts[-1].status if self.attempts else ""


def _attempt(run: AgentRun) -> LedgerAttempt:
    return LedgerAttempt(
        run_id=run.id,
        run_code=run.run_code,
        agent_id=run.agent_id,
        skill_name=run.skill_name,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def build_ledger(runs: list[AgentRun]) -> list[LedgerVisit]:
    """Group a ticket's runs into visits, oldest first.

    Ordering is by `created_at` rather than `started_at`: a queued run has no
    start time, and dropping it would hide work that is about to happen.
    """
    ordered = sorted(runs, key=lambda r: (r.created_at, r.run_code))
    visits: list[LedgerVisit] = []
    seen_counts: dict[str, int] = {}

    for run in ordered:
        current = visits[-1] if visits else None
        if current is not None and current.stage_key == run.stage_key:
            current.attempts.append(_attempt(run))
            continue
        seen_counts[run.stage_key] = seen_counts.get(run.stage_key, 0) + 1
        visits.append(
            LedgerVisit(
                stage_key=run.stage_key,
                attempts=[_attempt(run)],
                visit_number=seen_counts[run.stage_key],
            )
        )
    return visits


def ledger_payload(runs: list[AgentRun]) -> dict:
    """The ledger as JSON for the API, with the totals worth showing at a glance."""
    visits = build_ledger(runs)
    durations = [
        attempt.duration_seconds
        for visit in visits
        for attempt in visit.attempts
        if attempt.duration_seconds is not None
    ]
    return {
        "visits": [
            {
                "stage_key": visit.stage_key,
                "visit_number": visit.visit_number,
                "status": visit.status,
                "is_parallel": visit.is_parallel,
                "attempts": [
                    {
                        "run_id": a.run_id,
                        "run_code": a.run_code,
                        "agent_id": a.agent_id,
                        "skill_name": a.skill_name,
                        "status": a.status,
                        "started_at": a.started_at.isoformat() if a.started_at else None,
                        "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                        "duration_seconds": a.duration_seconds,
                    }
                    for a in visit.attempts
                ],
            }
            for visit in visits
        ],
        "total_runs": sum(len(v.attempts) for v in visits),
        # Stages entered more than once — the pipeline went backwards this often.
        "reworked_stages": sorted({v.stage_key for v in visits if v.visit_number > 1}),
        "total_seconds": round(sum(durations), 1) if durations else 0.0,
    }
