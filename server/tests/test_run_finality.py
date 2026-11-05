"""A run's first final status is its verdict (lg-run-durability-2093).

run_f4bf51 was failed by the lease sweep at 22:18 on 2026-11-03 and rewritten
`succeeded` at 22:41 by the agent the sweep had given up on, which also wiped the
stderr that said why it had failed. 65 runs on the live database had been
completed more than once.

- the guard: `complete_run` claims the final status by compare-and-swap and
  refuses a completion that arrives after one already landed;
- the invariant: `runs_complete_once` finds a run completed twice, and its repair
  writes the lost history onto the row.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    EventType,
    RunStatus,
    Ticket,
    WorkItemType,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.invariant_registry import RUNS_COMPLETE_ONCE
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_finality import OVERWRITE_NOTE
from loregarden.services.ticket_service import TicketService
from sqlalchemy.orm import undefer
from sqlmodel import Session, col, select


@pytest.fixture(name="ticket")
def ticket_fixture(db_session) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="run finality",
        work_item_type=WorkItemType.MILESTONE,
    )


@pytest.fixture(name="run")
def run_fixture(db_session, ticket) -> AgentRun:
    run = AgentRun(
        run_code="run_finality",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
        started_at=utcnow(),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def _complete(session: Session, run: AgentRun, status: RunStatus, **fields) -> AgentRun:
    return OrchestrationService(session).complete_run(
        run, status=status, advance_workflow=False, **fields
    )


def _completed_events(session: Session, run_id: str) -> list[DomainEvent]:
    return list(
        session.exec(
            select(DomainEvent)
            .where(DomainEvent.type == EventType.AGENT_RUN_COMPLETED)
            .where(DomainEvent.run_id == run_id)
            .order_by(col(DomainEvent.created_at))
        ).all()
    )


def _row(engine, run_id: str) -> AgentRun:
    with Session(engine) as probe:
        # Detached before the asserts read it, so the deferred transcript has
        # to be loaded here or not at all.
        row = probe.get(AgentRun, run_id, options=[undefer(AgentRun.stdout)])
        probe.expunge(row)
        return row


# ---- the guard ------------------------------------------------------------


def test_a_late_completion_leaves_the_verdict_and_publishes_nothing(
    db_session, run, isolated_db
) -> None:
    _complete(db_session, run, RunStatus.FAILED, stderr="Agent run lease expired")
    verdict = _row(isolated_db, run.id)

    late = _complete(db_session, run, RunStatus.SUCCEEDED, stdout="the late transcript")

    row = _row(isolated_db, run.id)
    assert late.status is RunStatus.FAILED
    assert row.status is RunStatus.FAILED
    assert row.stdout == verdict.stdout
    assert row.finished_at == verdict.finished_at
    assert row.stderr.startswith("Agent run lease expired")
    assert "reported succeeded after this run was already failed" in row.stderr
    assert [e.payload_json for e in _completed_events(db_session, run.id)] == [
        '{"status": "failed"}'
    ]


def test_a_completer_holding_a_stale_copy_still_loses(db_session, run, isolated_db) -> None:
    """The read-then-write race, made deterministic: this session loaded the run
    while it was RUNNING, and another connection finished it before this one
    tried. A guard that trusted the instance in hand would overwrite it."""
    assert db_session.get(AgentRun, run.id).status is RunStatus.RUNNING
    with Session(isolated_db) as other:
        _complete(other, other.get(AgentRun, run.id), RunStatus.CANCELLED, stderr="stopped")

    _complete(db_session, run, RunStatus.SUCCEEDED)

    assert _row(isolated_db, run.id).status is RunStatus.CANCELLED
    assert len(_completed_events(db_session, run.id)) == 1


def test_a_callers_unflushed_edit_does_not_defeat_the_guard(db_session, run, isolated_db) -> None:
    """Callers set fields on the run before completing it. An autoflush ahead of
    the guard would write the caller's status first and the guard would then
    refuse the very completion it should claim."""
    run.status = RunStatus.SUCCEEDED

    _complete(db_session, run, RunStatus.SUCCEEDED, stdout="done")

    row = _row(isolated_db, run.id)
    assert (row.status, row.stdout) == (RunStatus.SUCCEEDED, "done")
    assert len(_completed_events(db_session, run.id)) == 1


def test_an_in_flight_run_still_completes(db_session, run, isolated_db) -> None:
    """The control: the guard refuses late completions, not completions."""
    _complete(db_session, run, RunStatus.FAILED, stderr="boom")

    row = _row(isolated_db, run.id)
    assert (row.status, row.stderr) == (RunStatus.FAILED, "boom")
    assert "Late completion refused" not in row.stderr


# ---- the invariant --------------------------------------------------------


def _complete_twice_unguarded(db_session, run) -> None:
    """The state as it reached the live database before the guard: two
    completion events, the row showing the second."""
    first = utcnow() - timedelta(minutes=20)
    for status, at in (
        (RunStatus.FAILED, first),
        (RunStatus.SUCCEEDED, first + timedelta(minutes=5)),
    ):
        db_session.add(
            DomainEvent(
                type=EventType.AGENT_RUN_COMPLETED,
                workspace_id=run.workspace_id,
                ticket_id=run.ticket_id,
                run_id=run.id,
                payload_json=f'{{"status": "{status.value}"}}',
                created_at=at,
            )
        )
    run.status = RunStatus.SUCCEEDED
    run.stderr = ""
    db_session.add(run)
    db_session.commit()


def test_the_invariant_finds_a_run_completed_twice_dated_at_the_second(db_session, run) -> None:
    _complete_twice_unguarded(db_session, run)

    (violation,) = RUNS_COMPLETE_ONCE.detect(db_session)

    events = _completed_events(db_session, run.id)
    assert violation.subject_id == run.id
    assert violation.entered_at == events[1].created_at


def test_the_repair_writes_the_lost_verdict_back_and_converges(
    db_session, run, isolated_db
) -> None:
    _complete_twice_unguarded(db_session, run)
    violations = RUNS_COMPLETE_ONCE.detect(db_session)

    assert RUNS_COMPLETE_ONCE.repair(db_session, violations) == 1

    row = _row(isolated_db, run.id)
    assert row.status is RunStatus.SUCCEEDED, "the status was acted on; it stays"
    assert row.stderr.startswith(OVERWRITE_NOTE)
    assert "failed at" in row.stderr and "> succeeded at" in row.stderr
    assert RUNS_COMPLETE_ONCE.detect(db_session) == []


def test_a_guarded_double_completion_is_not_a_violation(db_session, run) -> None:
    """The guard and the detector agree: what the guard refuses leaves one event."""
    _complete(db_session, run, RunStatus.FAILED, stderr="first")
    _complete(db_session, run, RunStatus.SUCCEEDED)

    assert RUNS_COMPLETE_ONCE.detect(db_session) == []
