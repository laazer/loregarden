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

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from loregarden.config import settings
from loregarden.core.timestamps import iso_utc
from loregarden.models.domain import (
    AgentRun,
    CapacityPool,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    RunStatus,
)
from loregarden.services.run_errors import normalize_timeout_stderr
from sqlmodel import Session, col, select

#: Statuses that mean a run is still going, so its visit has no end yet.
_OPEN_STATUSES = frozenset({RunStatus.RUNNING, RunStatus.QUEUED})

#: How much of a failed run's stderr the ledger carries: the end, where the
#: error is. The whole text is on GET /runs/{id}/log.
ERROR_TAIL_LINES = 20
ERROR_TAIL_CHARS = 4000

#: Below this, a per-visit queue figure is not worth a line. Measured: one of
#: lg-reference-sources-1912's five gate leases was granted in 0.1s, and
#: "waited 0s for machine capacity" under a stage name costs a reader attention
#: and tells them nothing. The wait still counts in the ticket's total, which is
#: the ticket's real queue time.
CAPACITY_WAIT_FLOOR_SECONDS = 5.0


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


def error_tail(stderr: str) -> str:
    """The last lines of a run's stderr, where its error is."""
    lines = normalize_timeout_stderr(stderr).splitlines()[-ERROR_TAIL_LINES:]
    return "\n".join(lines)[-ERROR_TAIL_CHARS:]


def failed_run_error_tails(session: Session, runs: list[AgentRun]) -> dict[str, str]:
    """Each failed run's error tail, by run id, in one query.

    The run list defers stderr (it is a blob), so this selects it for the
    failed runs alone. A failed run whose stderr is empty is left out.
    """
    failed_ids = [run.id for run in runs if run.status == RunStatus.FAILED]
    if not failed_ids:
        return {}
    rows = session.exec(
        select(AgentRun.id, AgentRun.stderr).where(col(AgentRun.id).in_(failed_ids))
    ).all()
    tails = {run_id: error_tail(stderr or "") for run_id, stderr in rows}
    return {run_id: tail for run_id, tail in tails.items() if tail}


@dataclass(frozen=True)
class CapacityWaits:
    """How much of a ticket's wall-clock was queueing rather than working.

    Read from the HOST `docker_leases` rows that already carry the ticket's id,
    its `requested_at` and its `granted_at` — no new table and no new artifact
    kind. `run_ledger` read `agent_runs` and nothing else, so no surface on a
    ticket could show a capacity wait, which is half of the 1912 finding (8
    minutes of agent work, 29 minutes in the line) and the half nobody could
    see.

    Absent is not zero, and that is why `capacity_waits` returns None rather
    than an empty instance: with the ledger off, or on a ticket older than it,
    nothing was measured, and `0.0` would read as "this ticket never waited".
    """

    total_seconds: float
    lease_count: int
    reacquired_seconds: float
    #: Visit index (into `build_ledger`'s list) → seconds queued before it, for
    #: the visits whose attributed waits total at or above the floor.
    per_visit: Mapping[int, float] = field(default_factory=dict)
    #: Visit indexes whose attributed wait was a re-acquire after a park.
    after_park: frozenset[int] = frozenset()

    def payload(self) -> dict:
        return {
            "total_seconds": self.total_seconds,
            "lease_count": self.lease_count,
            "reacquired_seconds": self.reacquired_seconds,
        }


def _lease_wait(lease: DockerLease) -> float | None:
    """Seconds this claim spent in the line, or None if it never got capacity.

    An ungranted claim — a gate that gave up inside its budget and was released
    ABANDONED, or a claim still waiting as this payload is built — is not a trip
    through the line that cost nothing. The subtraction would raise on None, and
    calling it zero would report "this ticket did not wait" about the one row
    that is waiting.

    Clamped at zero: both stamps are written by whichever process held the
    lease, so a corrected clock or a row restored from a snapshot can put
    `granted_at` first, and a negative wait would subtract from the ticket's
    total — wrong in the direction nobody checks, on the surface added to make
    queueing visible.
    """
    if lease.granted_at is None or lease.requested_at is None:
        return None
    return max(0.0, (lease.granted_at - lease.requested_at).total_seconds())


def capacity_waits(session: Session, ticket_id: str, runs: list[AgentRun]) -> CapacityWaits | None:
    """This ticket's queue time, attributed per *visit*, or None if unmeasured.

    Per visit rather than per lease: the operator's question is "how much of
    this stage visit's wall-clock was queueing", and one live ticket holds 30
    gate leases (lg-external-harness-1026, 2867s), which per lease would be 30
    lines on a timeline that shows one row per visit. The rule is deterministic
    over rows that already exist: a lease belongs to the **earliest visit whose
    first attempt's `created_at` is at or after that lease's `granted_at`**, and
    a lease matching no visit (a transition after the last visit) counts in the
    total and in no visit — so the ticket's total can never disagree with the
    sum of its rows.

    Children are excluded **as children**, not because they happened to wait
    nothing. A gate child never queues, whatever its stamps read, and summing
    every HOST row would inflate `lease_count` by one per transition and make
    one 29-minute wait read as thirty small ones.
    """
    if not settings.docker_capacity_enabled:
        return None
    leases = list(
        session.exec(
            select(DockerLease).where(
                DockerLease.ticket_id == ticket_id,
                DockerLease.pool == CapacityPool.HOST,
                col(DockerLease.parent_lease_id).is_(None),
            )
        ).all()
    )
    if not leases:
        return None

    # Sorted by grant time so the park chain below reads in the order the run
    # actually paid the line, not in whatever order the rows came back.
    granted = sorted(
        ((lease, _lease_wait(lease)) for lease in leases),
        key=lambda pair: (pair[0].granted_at or pair[0].requested_at, pair[0].id),
    )
    granted = [(lease, wait) for lease, wait in granted if wait is not None]
    if not granted:
        return None

    starts = _visit_starts(runs)
    per_visit: dict[int, float] = {}
    after_park: set[int] = set()
    total = 0.0
    reacquired = 0.0
    previous_parent_park = False
    for lease, wait in granted:
        total += wait
        is_parent = lease.holder_kind is DockerHolderKind.ORCHESTRATION
        re_acquire = is_parent and previous_parent_park
        if re_acquire:
            reacquired += wait
        if is_parent:
            previous_parent_park = lease.end_reason is DockerLeaseEndReason.PARK_RELEASED
        index = _attributed_visit(starts, lease.granted_at)
        if index is not None:
            per_visit[index] = per_visit.get(index, 0.0) + wait
            if re_acquire:
                after_park.add(index)
    return CapacityWaits(
        total_seconds=round(total, 1),
        lease_count=len(granted),
        reacquired_seconds=round(reacquired, 1),
        per_visit={
            index: round(seconds, 1)
            for index, seconds in per_visit.items()
            if seconds >= CAPACITY_WAIT_FLOOR_SECONDS
        },
        after_park=frozenset(after_park),
    )


def _visit_starts(runs: list[AgentRun]) -> list[datetime]:
    """When each visit's first attempt was created, in `build_ledger`'s order."""
    ordered = sorted(runs, key=lambda r: (r.created_at, r.run_code))
    starts: list[datetime] = []
    previous_stage: str | None = None
    for run in ordered:
        if run.stage_key != previous_stage:
            starts.append(run.created_at)
            previous_stage = run.stage_key
    return starts


def _attributed_visit(starts: list[datetime], granted_at: datetime | None) -> int | None:
    """The earliest visit that began at or after this grant, or None."""
    if granted_at is None:
        return None
    stamp = granted_at.replace(tzinfo=None) if granted_at.tzinfo else granted_at
    for index, start in enumerate(starts):
        began = start.replace(tzinfo=None) if start.tzinfo else start
        if began >= stamp:
            return index
    return None


def ledger_payload(
    runs: list[AgentRun],
    error_tails: Mapping[str, str] | None = None,
    *,
    capacity_wait: CapacityWaits | None = None,
) -> dict:
    """The ledger as JSON for the API, with the totals worth showing at a glance.

    `error_tails` is `failed_run_error_tails`: a failed attempt's error, so a
    reader can tell whether its retries failed the same way.

    `capacity_wait` is `capacity_waits`, kept a separate read so this stays a
    pure function over rows: handing it a `Session` would make the only
    unit-testable part of the ledger need a database. None means nothing was
    measured, and every figure it feeds is then null rather than zero.
    """
    tails = error_tails or {}
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
                "capacity_wait_seconds": (
                    None if capacity_wait is None else capacity_wait.per_visit.get(index)
                ),
                "capacity_wait_after_park": (
                    capacity_wait is not None and index in capacity_wait.after_park
                ),
                "attempts": [
                    {
                        "run_id": a.run_id,
                        "run_code": a.run_code,
                        "agent_id": a.agent_id,
                        "skill_name": a.skill_name,
                        "status": a.status,
                        "started_at": iso_utc(a.started_at),
                        "finished_at": iso_utc(a.finished_at),
                        "duration_seconds": a.duration_seconds,
                        "error_tail": tails.get(a.run_id, ""),
                    }
                    for a in visit.attempts
                ],
            }
            for index, visit in enumerate(visits)
        ],
        "total_runs": sum(len(v.attempts) for v in visits),
        # Stages entered more than once — the pipeline went backwards this often.
        "reworked_stages": sorted({v.stage_key for v in visits if v.visit_number > 1}),
        "total_seconds": round(sum(durations), 1) if durations else 0.0,
        # Null, never 0.0: absent is not a measurement of nothing.
        "capacity_wait": None if capacity_wait is None else capacity_wait.payload(),
    }
