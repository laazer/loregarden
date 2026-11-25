"""Invariants: a start's idempotency key names the run it started, and only that."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import (
    InvariantReentry,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueueEntryKind,
)
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.start_keys import name_run, release_unstarted
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select


def _unstarted_holders(session: Session) -> list[Violation]:
    runs = session.exec(
        select(OrchestrationRun).where(
            col(OrchestrationRun.idempotency_key) != "",
            col(OrchestrationRun.started_at).is_(None),
            col(OrchestrationRun.status) == OrchestrationRunStatus.FAILED,
        )
    ).all()
    return [
        Violation(
            subject_id=run.id,
            ticket_id=run.ticket_id,
            entered_at=run.finished_at,
            detail=(
                f"{run.run_code} failed before anything started and still holds start key "
                f"{run.idempotency_key!r}, so a retry returns it instead of starting"
            ),
        )
        for run in runs
    ]


def _release_unstarted_holders(session: Session, violations: Sequence[Violation]) -> int:
    released = 0
    for violation in violations:
        run = session.get(OrchestrationRun, violation.subject_id)
        if run is None or not run.idempotency_key:
            continue
        release_unstarted(run)
        if run.idempotency_key:
            continue
        session.add(run)
        released += 1
    session.commit()
    return released


UNSTARTED_RUNS_HOLD_NO_START_KEY = Invariant(
    name="unstarted_runs_hold_no_start_key",
    ticket="lg-workflow-integrity-2797",
    statement="no orchestration run that failed before anything started holds a start's key",
    detect=_unstarted_holders,
    repair=_release_unstarted_holders,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 25),
    prevented_by=(
        "server/tests/test_orchestration_start_keys.py"
        "::test_every_way_a_claim_fails_unstarted_releases_its_key"
    ),
)


def _keyless_parked_runs(session: Session) -> list[Violation]:
    """Started runs whose lane entry was filed with a key no run of the ticket carries.

    A run already holding another key is not one: the entry was satisfied by
    work somebody else's start had opened, and a run carries one key.
    """
    holder = aliased(OrchestrationRun)
    rows = session.exec(
        select(QueuedRun, OrchestrationRun)
        .join(
            OrchestrationRun,
            col(OrchestrationRun.id) == col(QueuedRun.orchestration_run_id),
        )
        .where(
            col(QueuedRun.entry_kind) == QueueEntryKind.ORCHESTRATION,
            col(QueuedRun.idempotency_key) != "",
            col(OrchestrationRun.idempotency_key) == "",
            col(OrchestrationRun.started_at).is_not(None),
            ~select(holder.id)
            .where(
                holder.ticket_id == col(QueuedRun.ticket_id),
                holder.idempotency_key == col(QueuedRun.idempotency_key),
            )
            .exists(),
        )
    ).all()
    return [
        Violation(
            subject_id=entry.id,
            ticket_id=entry.ticket_id,
            entered_at=run.started_at,
            detail=(
                f"{run.run_code} was started from a lane entry filed with key "
                f"{entry.idempotency_key!r} and does not carry it, so a retry starts a second run"
            ),
        )
        for entry, run in rows
    ]


def _name_keyless_parked_runs(session: Session, violations: Sequence[Violation]) -> int:
    named = 0
    for violation in violations:
        entry = session.get(QueuedRun, violation.subject_id)
        run = (
            session.get(OrchestrationRun, entry.orchestration_run_id)
            if entry is not None and entry.orchestration_run_id
            else None
        )
        if entry is None or run is None or run.idempotency_key:
            continue
        name_run(session, run, entry.idempotency_key)
        named += bool(run.idempotency_key)
    session.commit()
    return named


PARKED_STARTS_KEEP_THEIR_KEY = Invariant(
    name="parked_starts_keep_their_key",
    ticket="lg-workflow-integrity-2797",
    statement="a run a lane started for a keyed start carries that start's key",
    detect=_keyless_parked_runs,
    repair=_name_keyless_parked_runs,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 25),
    prevented_by=(
        "server/tests/test_orchestration_start_keys.py"
        "::test_the_run_a_lane_starts_carries_the_parked_key"
    ),
)
