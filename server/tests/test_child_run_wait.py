"""A parent's child pass waits out a child run another dispatcher started.

On 2026-11-07 the prerequisite release started lg-milestone-that-2215 two
seconds before its parent's child pass reached it. The pass's start was
refused, the refusal reached the parent's run boundary, and the parent was
blocked for a person while the child finished its work unattended.
"""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.models.domain import (
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    TicketState,
)
from loregarden.services import builtin_orchestrator as mod
from loregarden.services.live_run_refusal_blocks import tickets_blocked_by_a_live_run_refusal
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_concurrency import OrchestrationAlreadyRunning
from loregarden.services.seed import seed_database
from sqlmodel import Session
from tests.factories import make_workspace_ticket


def _run(session: Session, ticket: Ticket, code: str, status: OrchestrationRunStatus):
    run = OrchestrationRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=status,
        driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@pytest.fixture(name="tree")
def tree_fixture(db_session: Session) -> tuple[Ticket, Ticket, OrchestrationRun, OrchestrationRun]:
    """A parent mid-drive, and a child whose run some other dispatcher already started."""
    seed_database(db_session)
    parent = make_workspace_ticket(db_session, "wait-parent")
    child = make_workspace_ticket(db_session, "wait-child")
    child.parent_ticket_id = parent.id
    db_session.add(child)
    db_session.commit()
    OrchestrationService(db_session).ensure_workflow_instance(child, commit=True)
    parent_run = _run(db_session, parent, "orch_parent", OrchestrationRunStatus.RUNNING)
    elsewhere = _run(db_session, child, "orch_elsewhere", OrchestrationRunStatus.RUNNING)
    return parent, child, parent_run, elsewhere


def test_a_start_refused_for_a_live_run_is_typed(db_session, tree):
    """The refusal the pass waits on is distinguishable from every other refusal."""
    _, child, _, _ = tree
    with pytest.raises(OrchestrationAlreadyRunning) as refused:
        mod.BuiltinOrchestrator(db_session).execute(child, mod.OrchestrationProfile(slug="t"))
    assert refused.value.run_code == "orch_elsewhere"


def test_the_pass_waits_for_the_other_run_and_judges_the_child_by_it(db_session, tree):
    parent, child, parent_run, elsewhere = tree

    def _other_run_finishes(_seconds):
        elsewhere.status = OrchestrationRunStatus.SUCCEEDED
        child.state = TicketState.DONE
        db_session.add_all([elsewhere, child])
        db_session.commit()

    with mock.patch("loregarden.services.child_run_wait.time.sleep", _other_run_finishes):
        reason = mod._orchestrate_incomplete_children(
            mod.BuiltinOrchestrator(db_session),
            parent,
            mod.OrchestrationProfile(slug="t"),
            parent_run=parent_run,
        )

    assert reason is None, "a child finished by its own run leaves nothing owed"
    db_session.refresh(parent)
    assert parent.state is not TicketState.BLOCKED
    db_session.refresh(parent_run)
    assert parent_run.last_seen_at is not None, "the waiting parent renews its lease"


def test_a_child_its_run_left_incomplete_pauses_the_parent_instead_of_blocking(db_session, tree):
    parent, child, parent_run, elsewhere = tree

    def _other_run_pauses(_seconds):
        elsewhere.status = OrchestrationRunStatus.SUCCEEDED
        elsewhere.error_message = "Paused after 3 stage(s)"
        db_session.add(elsewhere)
        db_session.commit()

    with mock.patch("loregarden.services.child_run_wait.time.sleep", _other_run_pauses):
        reason = mod._orchestrate_incomplete_children(
            mod.BuiltinOrchestrator(db_session),
            parent,
            mod.OrchestrationProfile(slug="t"),
            parent_run=parent_run,
        )

    assert reason is not None
    assert child.title in reason and "Paused after 3 stage(s)" in reason
    db_session.refresh(parent)
    assert parent.state is not TicketState.BLOCKED


def test_stopping_the_parent_stops_the_wait(db_session, tree):
    parent, child, parent_run, _ = tree
    with (
        mock.patch(
            "loregarden.services.child_run_wait.orchestration_cancel_requested", return_value=True
        ),
        mock.patch("loregarden.services.child_run_wait.time.sleep") as sleep,
    ):
        reason = mod._orchestrate_incomplete_children(
            mod.BuiltinOrchestrator(db_session),
            parent,
            mod.OrchestrationProfile(slug="t"),
            parent_run=parent_run,
        )

    sleep.assert_not_called()
    assert reason is not None and "orch_elsewhere" in reason and child.title in reason


def test_a_ticket_blocked_by_a_live_run_refusal_is_reported(db_session, tree):
    parent, _, _, _ = tree
    assert tickets_blocked_by_a_live_run_refusal(db_session) == []

    parent.state = TicketState.BLOCKED
    parent.blocking_issues = "Orchestration already running: orch_elsewhere"
    db_session.add(parent)
    db_session.commit()

    (violation,) = tickets_blocked_by_a_live_run_refusal(db_session)
    assert violation.subject_id == parent.id
    assert "orch_elsewhere" in violation.detail
