"""Blocks whose cause clears by itself resume without a person.

Over the 30 days to 2026-10-14: 22 stranded-stage settlements (several of them
on a live drive, between its runs) and 15 usage-limit escalations, each waiting
for somebody to requeue a stage nothing was wrong with.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestratorDecision,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    utcnow,
)
from loregarden.services import run_completion
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.parked_stage_resume import (
    resume_stranded_stages,
    resume_usage_limit_waits,
)
from loregarden.services.run_completion import advance_stage_after_run
from loregarden.services.run_service import (
    fail_interrupted_orchestration_runs,
    settle_stranded_stages,
)
from loregarden.services.seed import seed_database
from loregarden.services.stage_retry_budget import resolve_stage_retry_budget
from loregarden.services.stage_transient_retry import (
    count_transient_retries,
    record_transient_retry,
)
from loregarden.services.usage_limit_wait import (
    pending_usage_limit_waits,
    record_usage_limit_wait,
)
from loregarden.services.usage_limits import UsageLimit
from sqlmodel import Session, select
from tests.history_helpers import decision_payloads

STAGE = "testing"
SCHEDULE = "loregarden.services.parked_stage_resume.schedule_interrupted_resumes"


def _ticket(session: Session) -> Ticket:
    ticket = session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket is not None
    return ticket


@pytest.fixture
def session(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        yield session


@pytest.fixture
def driven(session):
    """A ticket under a live builtin drive whose run on STAGE has just ended."""
    ticket = _ticket(session)
    orch_run = OrchestrationCallbackService(session).start_orchestration_run(
        ticket, driver=OrchestrationDriver.BUILTIN_AUTOPILOT, profile_slug="default"
    )
    run = OrchestrationService(session).start_run(
        ticket, stage_key=STAGE, orchestration_run_id=orch_run.id
    )
    run.status = RunStatus.SUCCEEDED  # committed before the ticket is advanced
    session.add(run)
    session.commit()
    session.refresh(ticket)
    assert ticket.workflow_stage_status is StageStatus.RUNNING, "the fixture's premise"
    return ticket, orch_run, run


@pytest.fixture
def stranded(session, driven):
    """The same stage after its drive died: settled as stranded."""
    ticket, orch_run, _ = driven
    fail_interrupted_orchestration_runs(session, ticket_id=ticket.id)
    assert settle_stranded_stages(session, ticket_id=ticket.id) == [ticket]
    session.refresh(ticket)
    return ticket


# ---- 1: stranded stages ------------------------------------------------------


def test_a_stage_between_runs_of_a_live_drive_is_not_stranded(session, driven):
    ticket, orch_run, _ = driven
    assert settle_stranded_stages(session, ticket_id=ticket.id) == []
    session.refresh(ticket)
    assert ticket.workflow_stage_status is StageStatus.RUNNING
    assert orch_run.status is OrchestrationRunStatus.RUNNING


def test_a_stranded_stage_says_it_resumes_rather_than_that_it_waits(session, stranded):
    [escalation] = [
        p
        for p in decision_payloads(session, stranded.id)
        if p["decision"] == OrchestratorDecision.REPAIR_ESCALATED
    ]
    assert "re-runs it automatically" in escalation["reason"]
    assert "outside an orchestration run" not in escalation["reason"]


def test_a_stranded_stage_is_resumed_and_charged_to_its_retry_budget(session, stranded):
    with patch(SCHEDULE) as schedule:
        assert resume_stranded_stages(session) == [stranded.id]
    schedule.assert_called_once()
    session.refresh(stranded)
    assert stranded.workflow_stage_status is not StageStatus.BLOCKED
    assert stranded.state is TicketState.IN_PROGRESS
    assert count_transient_retries(session, stranded.id, STAGE) == 1


def test_a_stage_that_strands_every_time_stops_at_its_budget(session, stranded):
    budget = resolve_stage_retry_budget(session, stranded).max_transient_retries
    for _ in range(budget):
        record_transient_retry(
            session, stranded.id, STAGE, run_id="", reason="stranded_stage", message="x"
        )
    with patch(SCHEDULE):
        assert resume_stranded_stages(session) == []
    session.refresh(stranded)
    assert stranded.workflow_stage_status is StageStatus.BLOCKED


# ---- 2: usage limits -----------------------------------------------------------


def _limit(reset_in: timedelta | None) -> UsageLimit:
    return UsageLimit(
        provider="claude",
        reset_at=(utcnow() + reset_in) if reset_in is not None else None,
        reset_text="in 2 hours" if reset_in is not None else "",
        quote="You've hit your usage limit.",
    )


@pytest.mark.parametrize(("reset_in", "parked"), [(timedelta(hours=2), True), (None, False)])
def test_a_usage_limit_parks_the_stage_only_when_it_says_when_it_resets(
    session, driven, reset_in, parked
):
    ticket, _, run = driven
    with patch.object(run_completion, "run_usage_limit", return_value=_limit(reset_in)):
        advance_stage_after_run(
            OrchestrationService(session), ticket, run, None, RunStatus.SUCCEEDED, ""
        )
    assert bool(pending_usage_limit_waits(session)) is parked
    [escalation] = [
        p
        for p in decision_payloads(session, ticket.id)
        if p["decision"] == OrchestratorDecision.REPAIR_ESCALATED
    ]
    assert ("resumes automatically" in escalation["reason"]) is parked


@pytest.fixture
def limited(session, driven):
    """A stage blocked on a usage limit, its drive ended, a wait recorded."""
    ticket, _, run = driven
    with patch.object(run_completion, "run_usage_limit", return_value=_limit(timedelta(hours=2))):
        advance_stage_after_run(
            OrchestrationService(session), ticket, run, None, RunStatus.SUCCEEDED, ""
        )
    fail_interrupted_orchestration_runs(session, ticket_id=ticket.id)
    session.refresh(ticket)
    assert ticket.workflow_stage_status is StageStatus.BLOCKED, "the fixture's premise"
    return ticket, run


def test_a_wait_not_yet_due_is_kept(session, limited):
    with patch(SCHEDULE):
        assert resume_usage_limit_waits(session, now=utcnow()) == []
    assert len(pending_usage_limit_waits(session)) == 1


def test_the_stage_resumes_once_the_limit_has_reset(session, limited):
    ticket, _ = limited
    with patch(SCHEDULE) as schedule:
        assert resume_usage_limit_waits(session, now=utcnow() + timedelta(hours=3)) == [ticket.id]
    schedule.assert_called_once()
    assert pending_usage_limit_waits(session) == []
    session.refresh(ticket)
    assert ticket.workflow_stage_status is not StageStatus.BLOCKED


def test_a_wait_a_person_already_cleared_is_dropped_untouched(session, limited):
    ticket, _ = limited
    ticket.workflow_stage_status = StageStatus.DONE
    session.add(ticket)
    session.commit()
    with patch(SCHEDULE):
        assert resume_usage_limit_waits(session, now=utcnow() + timedelta(hours=3)) == []
    assert pending_usage_limit_waits(session) == []


def test_a_later_limit_on_the_same_stage_replaces_the_wait(session, limited):
    ticket, run = limited
    later = utcnow() + timedelta(days=6)
    record_usage_limit_wait(session, ticket.id, STAGE, run_id=run.id, reset_at=later)
    assert len(pending_usage_limit_waits(session)) == 1


def test_a_stage_whose_drive_a_person_cancelled_is_left_alone(session, stranded):
    """blob-core-absorption-147, live on 2026-10-14: stranded under a cancelled drive."""
    orch_run = session.exec(
        select(OrchestrationRun).where(OrchestrationRun.ticket_id == stranded.id)
    ).one()
    orch_run.status = OrchestrationRunStatus.CANCELLED
    session.add(orch_run)
    session.commit()
    with patch(SCHEDULE):
        assert resume_stranded_stages(session) == []
    session.refresh(stranded)
    assert stranded.workflow_stage_status is StageStatus.BLOCKED
    assert count_transient_retries(session, stranded.id, STAGE) == 0
