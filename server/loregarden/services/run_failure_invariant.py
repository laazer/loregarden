"""Invariant: a failed agent run has a `failure_kind`.

Kept apart from `run_failure` (the guard), which `orchestration` imports; the
invariant machinery reaches the orchestrator, so the guard must not import it
back.

The repair is also the backfill: the column arrived with one writer, so most
failed runs before the guard carry no kind. Those are backlog, classified at
boot from the reason each run already carries.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import AgentRun, InvariantReentry, RunStatus
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.run_errors import failure_reason
from loregarden.services.run_failure import classify_run_failure
from sqlmodel import Session, col, select


def failed_runs_without_a_kind(session: Session) -> list[Violation]:
    """FAILED runs whose `failure_kind` is NULL.

    `set_run_status` writes the kind with the status, so one here is a writer
    that went around it. Entered at the run's `finished_at`, else `started_at`
    (a run failed before it started has neither, and so reads as re-entry).
    """
    rows = session.exec(
        select(AgentRun.id, AgentRun.ticket_id, AgentRun.finished_at, AgentRun.started_at)
        .where(AgentRun.status == RunStatus.FAILED)
        .where(col(AgentRun.failure_kind).is_(None))
    ).all()
    return [
        Violation(
            subject_id=run_id,
            ticket_id=ticket_id,
            entered_at=finished_at or started_at,
            detail=f"agent run {run_id} failed with no failure_kind",
        )
        for run_id, ticket_id, finished_at, started_at in rows
    ]


def classify_failed_runs(session: Session, violations: Sequence[Violation]) -> int:
    """Classify each still-unkinded failed run from the reason it carries.

    The reason is the one `complete_run` would have written: `stderr`, or what
    `failure_reason` recovers from `stdout` when stderr is empty. Re-checks each
    run, since one may have been requeued or classified since detection.
    """
    repaired = 0
    for violation in violations:
        run = session.get(AgentRun, violation.subject_id)
        if run is None or run.status is not RunStatus.FAILED or run.failure_kind is not None:
            continue
        run.failure_kind = classify_run_failure(
            failure_reason(stderr=run.stderr or "", stdout=run.stdout or "")
        )
        session.add(run)
        repaired += 1
    session.commit()
    return repaired


FAILED_RUNS_HAVE_A_FAILURE_KIND = Invariant(
    name="failed_runs_have_a_failure_kind",
    ticket="lg-workflow-integrity-2746",
    statement="every failed agent run has a failure_kind",
    detect=failed_runs_without_a_kind,
    repair=classify_failed_runs,
    reentry=InvariantReentry.DEFECT,
    # The day after the guard was written: the live server keeps writing
    # unkinded failures until it restarts on the merged build.
    since=date(2026, 11, 19),
    prevented_by=(
        "server/tests/test_run_failure_kind.py"
        "::test_no_writer_sets_a_failed_status_around_set_run_status"
    ),
)
