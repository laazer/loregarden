"""A stage is not dispatched into an orchestration that has already been reaped.

`lg-workflow-integrity-688`. Observed live: orchestration 6cdb03dd was failed at
15:04:29 for an expired lease, a test-break run started against it at 15:04:54 —
25 seconds later — and `settle_orphaned_agent_runs` failed it at 15:05:07 with
"Parent orchestration is already terminal; this run was left in flight."

The ticket asked which layer noticed, and the answer was none of the three
guesses: not the dispatch, not the write-back, but a periodic SWEEPER catching it
afterwards. `start_run` checked the template and the ticket's terminal state and
never looked at the parent it was handed.

The cost is not the 13 wasted seconds. It is that the ticket ends up blocked at a
stage that never ran, with a reason describing plumbing, which a human then has
to tell apart from a real failure.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
)
from loregarden.services.orchestration import LIVE_ORCHESTRATION_STATUSES, OrchestrationService
from sqlmodel import Session
from tests.factories import make_workspace_ticket


def _orchestration(session: Session, ticket, status: OrchestrationRunStatus) -> OrchestrationRun:
    run = OrchestrationRun(
        run_code=f"orch_{status.value}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=status,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@pytest.mark.parametrize(
    "status",
    [
        OrchestrationRunStatus.FAILED,
        OrchestrationRunStatus.BLOCKED,
        OrchestrationRunStatus.CANCELLED,
        OrchestrationRunStatus.SUCCEEDED,
    ],
)
def test_a_reaped_parent_refuses_the_dispatch(db_session: Session, status: OrchestrationRunStatus):
    """Every terminal status, not just the FAILED one that was observed — a lease
    expiry produced FAILED here, but a cancelled or already-succeeded parent is
    just as unable to record the work."""
    ticket = make_workspace_ticket(db_session, f"dispatch-{status.value}")
    parent = _orchestration(db_session, ticket, status)

    with pytest.raises(ValueError, match="orchestration"):
        OrchestrationService(db_session).start_run(
            ticket,
            stage_key=ticket.workflow_stage_key,
            orchestration_run_id=parent.id,
            agent_id="planner",
        )


@pytest.mark.parametrize("status", list(LIVE_ORCHESTRATION_STATUSES))
def test_a_live_parent_still_dispatches(db_session: Session, status: OrchestrationRunStatus):
    """The guard must not refuse ordinary work. QUEUED counts as live: a claim is
    a promise that work is about to start, and refusing it would block the very
    dispatch it was made for."""
    ticket = make_workspace_ticket(db_session, f"dispatch-live-{status.value}")
    parent = _orchestration(db_session, ticket, status)

    run = OrchestrationService(db_session).start_run(
        ticket,
        stage_key=ticket.workflow_stage_key,
        orchestration_run_id=parent.id,
        agent_id="planner",
    )
    assert run.status in {RunStatus.QUEUED, RunStatus.RUNNING}
    assert run.orchestration_run_id == parent.id


def test_a_dispatch_with_no_parent_is_unaffected(db_session: Session):
    """Standalone dispatches carry no orchestration id and have their own guard —
    the retry budget. This must not start refusing them."""
    ticket = make_workspace_ticket(db_session, "dispatch-standalone")
    run = OrchestrationService(db_session).start_run(
        ticket, stage_key=ticket.workflow_stage_key, agent_id="planner"
    )
    assert run.orchestration_run_id in (None, "")


def test_an_id_naming_no_orchestration_is_refused_by_the_schema(db_session: Session):
    """The guard skips a parent it cannot find, on the reasoning that a dangling
    reference is a different fault from a reaped parent. That branch turns out to
    be unreachable through this path: foreign keys are enforced on every engine
    (PR #165), so the insert itself fails.

    Asserted rather than removed, because the guard's `parent is not None` check
    now looks like dead defensiveness and is not — the sweeper makes the same
    check, and the constraint that makes it unreachable lives in the schema
    rather than in this function.
    """
    from sqlalchemy.exc import IntegrityError

    ticket = make_workspace_ticket(db_session, "dispatch-ghost")
    with pytest.raises(IntegrityError):
        OrchestrationService(db_session).start_run(
            ticket,
            stage_key=ticket.workflow_stage_key,
            orchestration_run_id="no-such-orchestration",
            agent_id="planner",
        )
    db_session.rollback()
