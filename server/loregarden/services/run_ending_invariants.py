"""Invariants about how a run ends: once, and with nothing it held left held."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import InvariantReentry
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.orchestration_capacity import (
    release_terminal_run_parent,
    terminal_run_parents,
)
from loregarden.services.run_finality import completions, note_overwritten_runs, overwritten_runs
from sqlmodel import Session


def _stranded_run_parents(session: Session) -> list[Violation]:
    """Standing host leases whose orchestration is over.

    A run-scoped parent lease is the longest-lived claim on this machine — a run
    averages 17 minutes and the tail is 116 — so "held by a run that has
    finished" has a real window to enter. `entered_at` is the lease's grant
    time, which is the earliest this could have become true for that row.
    """
    return [
        Violation(
            subject_id=lease.id,
            ticket_id=lease.ticket_id,
            entered_at=lease.granted_at,
            detail=(
                f"{lease.holder_label} still holds {lease.cpus:g} cpus / "
                f"{lease.memory_mb} MB for an orchestration that has ended"
            ),
        )
        for lease in terminal_run_parents(session)
    ]


def _release_stranded_run_parents(session: Session, violations: Sequence[Violation]) -> int:
    return sum(
        1 for violation in violations if release_terminal_run_parent(session, violation.subject_id)
    )


RUN_PARENTS_END_WITH_THEIR_RUN = Invariant(
    name="run_parents_end_with_their_run",
    ticket="lg-machine-resource-1923",
    statement="no host capacity lease is held for an orchestration run that has ended",
    detect=_stranded_run_parents,
    repair=_release_stranded_run_parents,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 11, 2),
    expected_because=(
        "`BuiltinOrchestrator.execute()`'s one `finally` makes this unreachable through the "
        "driver, but a SIGKILL of the server skips every `finally` there is — the world "
        "entering the state legitimately rather than a writer failing to guard it"
    ),
    # Set even though EXPECTED does not require it: the guard is real and this is
    # the test that proves it, across every exit from execute().
    prevented_by=(
        "server/tests/test_orchestration_capacity.py"
        "::test_no_park_leaves_a_held_parent_after_execute_returns"
    ),
)


def _overwritten_runs(session: Session) -> list[Violation]:
    """`entered_at` is the second completion: the moment the first verdict was lost."""
    violations = []
    for run in overwritten_runs(session):
        events = completions(session, run.id)
        violations.append(
            Violation(
                subject_id=run.id,
                ticket_id=run.ticket_id,
                entered_at=events[1].created_at if len(events) > 1 else None,
                detail=f"{run.run_code} completed {len(events)} times; the row shows the last "
                f"({run.status.value})",
            )
        )
    return violations


def _note_overwritten_runs(session: Session, violations: Sequence[Violation]) -> int:
    return note_overwritten_runs(session, [violation.subject_id for violation in violations])


RUNS_COMPLETE_ONCE = Invariant(
    name="runs_complete_once",
    ticket="lg-run-durability-2093",
    statement="no agent run is completed after it already has a final status",
    detect=_overwritten_runs,
    repair=_note_overwritten_runs,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 4),
    prevented_by=(
        "server/tests/test_run_finality.py"
        "::test_a_late_completion_leaves_the_verdict_and_publishes_nothing"
    ),
)
