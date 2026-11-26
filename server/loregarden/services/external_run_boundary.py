"""External-harness runs that finished with no record of the tree they started on.

The terminal handoff stamped a run's start boundary; the external-harness
checkout did not, so all 415 external runs on live before the fix carry none.
Every reader treats a missing boundary as unknown (a lens re-runs, a recheck
names `<unknown>` as its base), so the gap cost carried-forward passes and
attribution, not correctness — and the commit a past run started on cannot be
recovered, which is why this reports rather than repairs.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from loregarden.models.domain import AgentRun, InvariantReentry, RunStatus
from loregarden.services.invariants import Invariant, Violation
from sqlmodel import Session, col, select

_SINCE = date(2026, 11, 27)


def external_runs_with_no_start_boundary(session: Session) -> list[Violation]:
    """Only runs opened since the guard: older ones are the unrecoverable backlog
    the module docstring describes, and reporting them would say nothing new on
    every pass. `entered_at` is the run's `created_at`."""
    since = datetime(_SINCE.year, _SINCE.month, _SINCE.day, tzinfo=timezone.utc)
    rows = session.exec(
        select(AgentRun).where(
            col(AgentRun.external_harness).is_not(None),
            AgentRun.status == RunStatus.SUCCEEDED,
            AgentRun.start_head_sha == "",
            col(AgentRun.created_at) >= since.replace(tzinfo=None),
        )
    ).all()
    return [
        Violation(
            subject_id=run.id,
            ticket_id=run.ticket_id,
            entered_at=run.created_at,
            detail=f"run {run.run_code} ({run.stage_key}) finished with no start commit",
        )
        for run in rows
    ]


EXTERNAL_RUNS_RECORD_THEIR_START = Invariant(
    name="external_runs_record_their_start",
    ticket="lg-verdict-outcome-2756",
    statement="every external-harness run that finished records the commit it started on",
    detect=external_runs_with_no_start_boundary,
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=_SINCE,
    prevented_by=(
        "server/tests/test_external_harness_parallel_stage.py"
        "::test_each_member_records_the_commit_it_started_on_and_keeps_it"
    ),
    unrepaired_action=(
        "Nothing to recover: the commit the run started on is gone. Find the "
        "checkout path that handed this run out without stamping its boundary "
        "and stamp it there."
    ),
)
