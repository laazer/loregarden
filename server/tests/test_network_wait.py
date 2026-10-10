"""A stage that cannot reach the provider waits for the network, then resumes.

Retrying immediately during an outage spends the whole budget inside it: 19 runs
across three tickets on 2026-10-09, every one `ENOTFOUND`, then ten hours
blocked until a person requeued them.
"""

from unittest.mock import MagicMock, patch

import pytest
from loregarden.models.domain import (
    BlockKind,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    TicketState,
)
from loregarden.services.network_wait import (
    is_network_unreachable,
    pending_network_waits,
)
from loregarden.services.network_wait_resume import resume_network_waits
from loregarden.services.stage_transient_retry import count_transient_retries
from sqlmodel import Session
from tests.test_transient_retry import _die, _ticket_on_stage

# The Claude CLI's terminal line from the outage, trimmed to what matters.
ENOTFOUND_STDOUT = (
    '{"type":"result","is_error":true,"terminal_reason":"api_error",'
    '"result":"API Error: Can\'t reach the API server — check your internet or DNS '
    '(ENOTFOUND)","uuid":"x"}'
)


@pytest.fixture(name="offline")
def offline_fixture():
    with patch("loregarden.services.run_completion.provider_reachable", return_value=False):
        yield


@pytest.fixture(name="online")
def online_fixture():
    with patch("loregarden.services.run_completion.provider_reachable", return_value=True):
        yield


def test_unreachable_signatures_are_named_and_others_are_not():
    assert is_network_unreachable(ENOTFOUND_STDOUT, "")
    assert is_network_unreachable("", "getaddrinfo EAI_AGAIN api.anthropic.com")
    assert not is_network_unreachable("", "429 Too Many Requests: rate limit")


def test_an_outage_parks_the_stage_without_spending_a_retry(db_session: Session, offline):
    ticket = _ticket_on_stage(db_session)

    _die(db_session, ticket, stdout=ENOTFOUND_STDOUT)

    assert ticket.workflow_stage_status is StageStatus.BLOCKED
    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.HARNESS
    assert count_transient_retries(db_session, ticket.id, "testing") == 0
    assert [m.ticket_id for m in pending_network_waits(db_session)] == [ticket.id]


def test_when_this_machine_can_reach_the_provider_the_retry_runs_as_before(
    db_session: Session, online
):
    ticket = _ticket_on_stage(db_session)

    _die(db_session, ticket, stdout=ENOTFOUND_STDOUT)

    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert count_transient_retries(db_session, ticket.id, "testing") == 1
    assert pending_network_waits(db_session) == []


def _previous_run(db_session: Session, ticket, *, auto_approve: bool) -> None:
    db_session.add(
        OrchestrationRun(
            run_code="orch_prev",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            auto_approve=auto_approve,
            status=OrchestrationRunStatus.BLOCKED,
        )
    )
    db_session.commit()


def test_the_parked_stage_resumes_on_its_old_terms_once_the_network_is_back(
    db_session: Session, offline
):
    ticket = _ticket_on_stage(db_session)
    _die(db_session, ticket, stdout=ENOTFOUND_STDOUT)
    _previous_run(db_session, ticket, auto_approve=True)
    schedule = MagicMock()

    with patch("loregarden.services.network_wait_resume.schedule_interrupted_resumes", schedule):
        assert resume_network_waits(db_session, probe=lambda: False) == []
        assert pending_network_waits(db_session) != [], "still offline: nothing moves"
        handled = resume_network_waits(db_session, probe=lambda: True)

    db_session.refresh(ticket)
    assert handled == [ticket.id]
    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert ticket.blocking_issues == ""
    assert pending_network_waits(db_session) == []
    (requests,) = schedule.call_args.args
    assert [(r.ticket_id, r.auto_approve) for r in requests] == [(ticket.id, True)]


def test_a_ticket_a_person_already_moved_on_is_left_alone(db_session: Session, offline):
    ticket = _ticket_on_stage(db_session)
    _die(db_session, ticket, stdout=ENOTFOUND_STDOUT)
    ticket.workflow_stage_status = StageStatus.DONE
    db_session.add(ticket)
    db_session.commit()
    schedule = MagicMock()

    with patch("loregarden.services.network_wait_resume.schedule_interrupted_resumes", schedule):
        handled = resume_network_waits(db_session, probe=lambda: True)

    assert handled == []
    assert pending_network_waits(db_session) == []
    db_session.refresh(ticket)
    assert ticket.workflow_stage_status is StageStatus.DONE


def test_nothing_waiting_means_no_probe():
    probe = MagicMock(return_value=True)
    session = MagicMock()
    session.exec.return_value.all.return_value = []

    assert resume_network_waits(session, probe=probe) == []
    probe.assert_not_called()
