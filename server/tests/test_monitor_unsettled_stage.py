"""A stage blocked while its own run recorded success.

`lg-workflow-integrity-692`. Observed live: run_2a77c3 recorded `succeeded` and
the stage it ran still sat `blocked`, and a human reconciled the two by hand.
`complete_run` commits the run's terminal status before it touches the ticket,
so any failure in between leaves exactly this residue — and under a starved
gateway (load 158 on 2026-09-09) that gap is where write-backs die.

`settle_stranded_stages` does not cover it: that sweep selects stages stuck
RUNNING and settles them TO blocked. A stage already blocked is invisible to it.

The discriminator carries the weight here. "The run succeeded" and "the stage
should pass" are different claims — a reviewer that ran fine and rejected the
work also produces a succeeded run against a blocked stage. Most of these tests
are about not reporting that one.
"""

from __future__ import annotations

from loregarden.models.domain import (
    AUTO_FIXABLE_CONDITIONS,
    AgentRun,
    MonitorCondition,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.run_interruption import (
    ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
    STRANDED_STAGE_MESSAGE,
)
from loregarden.services.workflow_monitor import scan
from sqlmodel import Session
from tests.factories import make_workspace_ticket

STAGE = "implement"


def _blocked_ticket(session: Session, name: str, *, blocking: str) -> Ticket:
    ticket = make_workspace_ticket(session, name)
    ticket.workflow_stage_key = STAGE
    ticket.workflow_stage_status = StageStatus.BLOCKED
    ticket.blocking_issues = blocking
    session.add(ticket)
    session.commit()
    return ticket


def _run(session: Session, ticket: Ticket, *, status: RunStatus, code: str) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key=STAGE,
        status=status,
    )
    session.add(run)
    session.commit()
    return run


def _unsettled(findings) -> list:
    return [f for f in findings if f.condition == MonitorCondition.UNSETTLED_STAGE]


def test_a_lost_write_back_is_reported(db_session: Session):
    """AC2. The observed case: blocked stage, succeeded run, plumbing message."""
    ticket = _blocked_ticket(db_session, "unsettled-1", blocking=STRANDED_STAGE_MESSAGE)
    _run(db_session, ticket, status=RunStatus.SUCCEEDED, code="run_lost")

    found = _unsettled(scan(db_session, ticket_id=ticket.id))
    assert len(found) == 1
    assert found[0].stage_key == STAGE
    assert found[0].evidence["run_code"] == "run_lost"


def test_an_orphaned_parent_counts_as_plumbing_too(db_session: Session):
    """A parent going terminal underneath a live run is the same kind of event
    as a reload artifact, and leaves the same residue."""
    ticket = _blocked_ticket(db_session, "unsettled-2", blocking=ORPHAN_OF_TERMINAL_ORCH_MESSAGE)
    _run(db_session, ticket, status=RunStatus.SUCCEEDED, code="run_orphan")

    assert len(_unsettled(scan(db_session, ticket_id=ticket.id))) == 1


def test_a_verdict_is_not_a_lost_write_back(db_session: Session):
    """AC3, and the reason this detector is narrow.

    A reviewer that ran cleanly and rejected the work leaves a succeeded run
    against a blocked stage — identical on those two columns to the defect.
    Reporting it would invite advancing a ticket past a rejection nobody
    overruled. The blocking text is what tells them apart.
    """
    ticket = _blocked_ticket(
        db_session,
        "unsettled-3",
        blocking="Security review rejected: credentials are logged in plaintext.",
    )
    _run(db_session, ticket, status=RunStatus.SUCCEEDED, code="run_reviewed")

    assert _unsettled(scan(db_session, ticket_id=ticket.id)) == []


def test_a_stage_with_no_succeeded_run_is_left_alone(db_session: Session):
    """Blocked with nothing behind it is a stage that genuinely has not run."""
    ticket = _blocked_ticket(db_session, "unsettled-4", blocking=STRANDED_STAGE_MESSAGE)
    _run(db_session, ticket, status=RunStatus.FAILED, code="run_failed")

    assert _unsettled(scan(db_session, ticket_id=ticket.id)) == []


def test_a_succeeded_run_on_another_stage_does_not_count(db_session: Session):
    """The run has to be for the stage that is blocked, or every ticket with any
    successful history would report."""
    ticket = _blocked_ticket(db_session, "unsettled-5", blocking=STRANDED_STAGE_MESSAGE)
    run = AgentRun(
        run_code="run_elsewhere",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="planner",
        stage_key="plan",
        status=RunStatus.SUCCEEDED,
    )
    db_session.add(run)
    db_session.commit()

    assert _unsettled(scan(db_session, ticket_id=ticket.id)) == []


def test_a_finished_ticket_is_not_reported(db_session: Session):
    """A done ticket's stage rows are history, not a backlog of repairs."""
    ticket = _blocked_ticket(db_session, "unsettled-6", blocking=STRANDED_STAGE_MESSAGE)
    _run(db_session, ticket, status=RunStatus.SUCCEEDED, code="run_done")
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()

    assert _unsettled(scan(db_session, ticket_id=ticket.id)) == []


def test_the_condition_is_not_auto_fixable(db_session: Session):
    """AC4, pinned as a fact about the enum rather than a promise in a docstring.

    Advancing a stage is a judgement about whether the work passed, and a run's
    status does not carry that. It stays report-only until a human or
    lg-workflow-integrity-566's policy decides otherwise.
    """
    assert MonitorCondition.UNSETTLED_STAGE not in AUTO_FIXABLE_CONDITIONS


def test_scanning_changes_nothing(db_session: Session):
    """The monitor observes; it never repairs. Asserted here too because this
    detector is the first one whose subject is a stage it could be tempted to
    advance."""
    ticket = _blocked_ticket(db_session, "unsettled-7", blocking=STRANDED_STAGE_MESSAGE)
    _run(db_session, ticket, status=RunStatus.SUCCEEDED, code="run_readonly")

    scan(db_session, ticket_id=ticket.id)
    db_session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.BLOCKED
    assert ticket.blocking_issues == STRANDED_STAGE_MESSAGE
