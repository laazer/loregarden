"""A park card answers for its stage only while that stage is parked.

2ce552c3 on lmkr-execution-leaves-11 parked `plan` for a missing report
contract. The ticket was requeued once that was fixed, the planner ran and
passed, and the card stayed pending: its Reject ("Leave parked") and Ignore
would have set the stage BLOCKED, and its Approve re-pended it.
"""

from unittest import mock

import pytest
from loregarden.models.domain import (
    ApprovalStatus,
    OrchestrationDriver,
    RunStatus,
    StageStatus,
)
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.stale_park_invariant import (
    close_stale_park_violations,
    stale_parks,
)
from loregarden.services.stale_stage_parks import SUPERSEDED_BY, stage_status
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session
from tests.test_stage_parking import parked_fixture  # noqa: F401  (the `parked` fixture)


def _move_stage(session: Session, ticket, stage_key: str, status: StageStatus) -> None:
    """Take the parked stage out of AWAITING the way a requeue does: no dispatch."""
    orch = OrchestrationService(session)
    instance, stages = orch._resolve_stages(ticket)
    set_stage_status(ticket, instance, stages, stage_key, status)
    session.add(ticket)
    session.add(instance)
    session.commit()


def _settle(session: Session, run) -> None:
    OrchestrationService(session).complete_run(run, status=RunStatus.CANCELLED)


@pytest.fixture(name="requeued")
def requeued_fixture(db_session: Session, parked):
    """The parked ticket after a requeue: the stage is PENDING, the card still pending."""
    ticket, run, approval, stage_key = parked
    _settle(db_session, run)
    _move_stage(db_session, ticket, stage_key, StageStatus.PENDING)
    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.PENDING
    return ticket, approval, stage_key


@pytest.mark.parametrize("approved", [True, False])
def test_answering_a_stale_park_is_refused_and_changes_nothing(db_session, requeued, approved):
    ticket, approval, stage_key = requeued

    with (
        mock.patch("loregarden.services.orchestration_resume.schedule_orchestration"),
        pytest.raises(ValueError, match=f"'{stage_key}', which is pending now.*Ignore it"),
    ):
        ApprovalService(db_session).resolve(approval.id, approved=approved)

    db_session.refresh(approval)
    db_session.refresh(ticket)
    assert approval.status is ApprovalStatus.PENDING
    assert stage_status(db_session, ticket, stage_key) is StageStatus.PENDING
    assert not ticket.dispatch_waiver_stage_key


def test_ignoring_a_stale_park_closes_it_without_blocking_the_stage(db_session, requeued):
    ticket, approval, stage_key = requeued

    ApprovalService(db_session).ignore(approval.id, reason="stale: requeued since")

    db_session.refresh(approval)
    db_session.refresh(ticket)
    assert approval.status is ApprovalStatus.IGNORED
    assert stage_status(db_session, ticket, stage_key) is StageStatus.PENDING
    assert not ticket.blocking_issues


def test_ignoring_a_live_park_still_declines_the_stage(db_session, parked):
    """The fix must not turn every Ignore into a no-op: a park that is still
    parked is declined, as before."""
    ticket, _, approval, stage_key = parked

    ApprovalService(db_session).ignore(approval.id, reason="not now")

    db_session.refresh(ticket)
    assert stage_status(db_session, ticket, stage_key) is StageStatus.BLOCKED


def test_dispatching_the_stage_closes_its_park(db_session, parked):
    ticket, run, approval, stage_key = parked
    _settle(db_session, run)

    OrchestrationService(db_session).start_run(ticket, stage_key=stage_key)

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.IGNORED
    assert approval.resolved_by == SUPERSEDED_BY


def test_starting_the_stage_over_mcp_closes_its_park(db_session, parked):
    ticket, run, approval, stage_key = parked
    _settle(db_session, run)
    callbacks = OrchestrationCallbackService(db_session)
    orch_run = callbacks.start_orchestration_run(
        ticket, driver=OrchestrationDriver.EXTERNAL_MCP, profile_slug="default"
    )

    callbacks.start_stage(orch_run, ticket, stage_key=stage_key)

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.IGNORED
    assert approval.resolved_by == SUPERSEDED_BY


def test_the_invariant_closes_a_stale_park_and_leaves_a_live_one(db_session, parked):
    ticket, run, approval, stage_key = parked
    assert stale_parks(db_session) == []

    _settle(db_session, run)
    _move_stage(db_session, ticket, stage_key, StageStatus.PENDING)
    found = stale_parks(db_session)

    assert [v.subject_id for v in found] == [approval.id]
    assert close_stale_park_violations(db_session, found) == 1
    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.IGNORED
    assert stale_parks(db_session) == []
