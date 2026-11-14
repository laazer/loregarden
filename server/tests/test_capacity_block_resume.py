"""A ticket a capacity wait blocked resumes once the machine's line clears.

lg-milestone-that-1425 (2026-11-13): its verify run succeeded, the criteria
check behind it could not get host capacity while two pre-push suites held the
line, and it sat blocked for a person who had nothing to decide.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    BlockKind,
    BlockOrigin,
    DockerLease,
    DockerLeaseStatus,
    OrchestrationDriver,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.block_settlement import RepairPolicy
from loregarden.services.capacity_block_resume import (
    CAPACITY_RETRY_REASON,
    cleared_capacity_violations,
    resume_cleared_capacity_blocks,
)
from loregarden.services.gate_capacity import GateCapacityUnavailable
from loregarden.services.invariant_registry import CAPACITY_BLOCKS_RESUME, INVARIANTS
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.seed import seed_database
from loregarden.services.stage_retry_budget import resolve_stage_retry_budget
from loregarden.services.stage_transient_retry import (
    count_transient_retries,
    record_transient_retry,
)
from sqlmodel import Session, select

STAGE = "testing"
SCHEDULE = "loregarden.services.capacity_block_resume.schedule_interrupted_resumes"


@pytest.fixture
def session(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        yield session


def _block(session: Session, *, message: str, kind: BlockKind = BlockKind.HARNESS) -> Ticket:
    """A ticket whose STAGE run succeeded, then blocked by its drive the way the
    criteria check blocks: under the orchestration, with the message given."""
    ticket = session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket is not None
    callbacks = OrchestrationCallbackService(session)
    orch_run = callbacks.start_orchestration_run(
        ticket, driver=OrchestrationDriver.BUILTIN_AUTOPILOT, profile_slug="default"
    )
    run = OrchestrationService(session).start_run(
        ticket, stage_key=STAGE, orchestration_run_id=orch_run.id
    )
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    callbacks.block_ticket(
        orch_run,
        ticket,
        origin=BlockOrigin.CONTROL_PLANE,
        repair=RepairPolicy.withheld("Another agent turn cannot change what the check found."),
        message=message,
        declared=kind,
    )
    session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED, "the fixture's premise"
    assert ticket.workflow_stage_status is StageStatus.BLOCKED, "the fixture's premise"
    return ticket


def _capacity_message() -> str:
    refusal = GateCapacityUnavailable(
        "Gate commands for criteria check did not run: no service host capacity "
        "(the line stopped moving)"
    )
    return f"The criteria check could not run: {refusal}"


def _waiting_lease(session: Session) -> None:
    session.add(DockerLease(status=DockerLeaseStatus.WAITING, holder_label="pre-push", position=1))
    session.commit()


def _resume(session: Session) -> int:
    with patch(SCHEDULE):
        return resume_cleared_capacity_blocks(session, cleared_capacity_violations(session))


def test_a_capacity_block_resumes_once_the_line_is_clear(session):
    ticket = _block(session, message=_capacity_message())
    [violation] = cleared_capacity_violations(session)
    assert violation.subject_id == ticket.id
    with patch(SCHEDULE) as schedule:
        assert resume_cleared_capacity_blocks(session, [violation]) == 1
    schedule.assert_called_once()
    session.refresh(ticket)
    assert ticket.state is TicketState.IN_PROGRESS
    assert ticket.workflow_stage_status is not StageStatus.BLOCKED
    assert count_transient_retries(session, ticket.id, STAGE) == 1


def test_it_waits_while_anything_is_still_waiting_in_line(session):
    ticket = _block(session, message=_capacity_message())
    _waiting_lease(session)
    assert cleared_capacity_violations(session) == []
    assert _resume(session) == 0
    session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED


def test_a_block_that_is_not_a_capacity_wait_is_left_alone(session):
    _block(session, message="The agent's CLI could not authenticate.")
    assert cleared_capacity_violations(session) == []


def test_a_work_block_is_left_alone(session):
    _block(session, message=_capacity_message(), kind=BlockKind.WORK)
    assert cleared_capacity_violations(session) == []


def test_a_ticket_a_person_locked_is_left_alone(session):
    ticket = _block(session, message=_capacity_message())
    ticket.state_locked = True
    session.add(ticket)
    session.commit()
    assert cleared_capacity_violations(session) == []


def test_a_stage_that_never_gets_capacity_stops_at_its_retry_budget(session):
    ticket = _block(session, message=_capacity_message())
    budget = resolve_stage_retry_budget(session, ticket).max_transient_retries
    for _ in range(budget):
        record_transient_retry(
            session, ticket.id, STAGE, run_id="", reason=CAPACITY_RETRY_REASON, message="x"
        )
    assert cleared_capacity_violations(session) == []
    session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED


def test_the_resume_is_a_registered_expected_invariant():
    assert CAPACITY_BLOCKS_RESUME in INVARIANTS
    assert CAPACITY_BLOCKS_RESUME.repair is not None
