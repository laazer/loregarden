"""A blocked run has to be able to say why.

29 of 74 blocked orchestration runs carried an empty `error_message`, so a third
of the blocked history cannot be diagnosed at all. The reason usually existed at
the time — the ticket's `blocking_issues` held it — but that field is cleared
when a ticket resumes, so a message not copied at completion is not recoverable
afterwards (lg-workflow-integrity-90).
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    OrchestrationRunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from sqlmodel import Session


@pytest.fixture
def ticket(db_session: Session) -> Ticket:
    ws = Workspace(slug="blk", name="Blk", repo_path="/nonexistent/blk")
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    t = Ticket(
        external_id="blk-1",
        workspace_id=ws.id,
        title="Blocked target",
        state=TicketState.BLOCKED,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


def test_the_tickets_own_reason_is_copied_onto_the_run(ticket):
    """The common case: the ticket knows, and the run should say the same thing."""
    ticket.blocking_issues = "Agent run exited successfully but emitted no parseable report"

    message = BuiltinOrchestrator._completion_message(
        OrchestrationRunStatus.BLOCKED, ticket, cancelled=False
    )
    assert message == "Agent run exited successfully but emitted no parseable report"


def test_a_blocked_run_with_nothing_on_the_ticket_still_says_something(ticket):
    """5 of the 8 most recent message-less rows had no `blocking_issues` either.
    An empty string cannot distinguish "we looked and found nothing" from "nobody
    recorded anything", and that ambiguity is the defect."""
    ticket.blocking_issues = ""

    message = BuiltinOrchestrator._completion_message(
        OrchestrationRunStatus.BLOCKED, ticket, cancelled=False
    )
    assert message
    assert "implement" in message
    assert "no reason recorded" in message


def test_a_succeeded_run_is_left_alone(ticket):
    """This is about blocked runs. A success carrying an error message would be
    its own kind of lie."""
    ticket.blocking_issues = "stale text from an earlier block"

    assert (
        BuiltinOrchestrator._completion_message(
            OrchestrationRunStatus.SUCCEEDED, ticket, cancelled=False
        )
        == ""
    )


def test_cancellation_still_wins(ticket):
    ticket.blocking_issues = "something else"
    assert (
        BuiltinOrchestrator._completion_message(
            OrchestrationRunStatus.BLOCKED, ticket, cancelled=True
        )
        == "Cancelled by operator"
    )


def test_the_backfill_recovers_a_reason_and_marks_the_rest(db_session):
    """AC2. Rows already on disk, marked rather than invented."""
    from loregarden.db.migrations_blocked_run_reason import m_blocked_run_reason
    from loregarden.models.domain import OrchestrationRun
    from sqlmodel import select

    ws = Workspace(slug="bf", name="BF", repo_path="/nonexistent/bf")
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    recoverable = Ticket(
        external_id="bf-1",
        workspace_id=ws.id,
        title="Has reason",
        state=TicketState.BLOCKED,
        work_item_type=WorkItemType.TASK,
        blocking_issues="Gate failed on ruff errors",
    )
    lost = Ticket(
        external_id="bf-2",
        workspace_id=ws.id,
        title="Lost reason",
        state=TicketState.BLOCKED,
        work_item_type=WorkItemType.TASK,
    )
    db_session.add(recoverable)
    db_session.add(lost)
    db_session.commit()
    db_session.refresh(recoverable)
    db_session.refresh(lost)

    for code, tkt in (("bf_a", recoverable), ("bf_b", lost)):
        db_session.add(
            OrchestrationRun(
                ticket_id=tkt.id,
                workspace_id=ws.id,
                run_code=code,
                status=OrchestrationRunStatus.BLOCKED,
                error_message="",
            )
        )
    kept = OrchestrationRun(
        ticket_id=lost.id,
        workspace_id=ws.id,
        run_code="bf_c",
        status=OrchestrationRunStatus.BLOCKED,
        error_message="already recorded",
    )
    db_session.add(kept)
    db_session.commit()

    m_blocked_run_reason(db_session.connection())
    db_session.commit()

    rows = {r.run_code: r.error_message for r in db_session.exec(select(OrchestrationRun)).all()}
    assert rows["bf_a"] == "Gate failed on ruff errors"
    assert "Reason not recorded" in rows["bf_b"]
    # Never overwrite a message somebody already has.
    assert rows["bf_c"] == "already recorded"
