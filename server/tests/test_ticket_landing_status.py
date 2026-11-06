"""What a landing ticket is waiting on, read from the ledger.

The ticket page said "Landing…" for the hours lg-milestone-that-2250 spent
being refused and retried. These pin each phase to the rows that produce it,
and the refusal streak to the receipts.
"""

from datetime import timedelta

import pytest
from loregarden.models.domain import (
    ArtifactKind,
    DockerFootprint,
    DockerHolderKind,
    DockerLease,
    DockerLeaseStatus,
    GateOutcome,
    LandingPhase,
    OrchestrationRunStatus,
    TicketState,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.docker_ledger import as_utc
from loregarden.services.land_verification import (
    LAND_VERIFY_TIMEOUT_SECONDS,
    LAND_VERIFY_TRANSITION,
    RECEIPT_DISABLED,
    RECEIPT_FAILED,
    RECEIPT_PASSED,
)
from loregarden.services.landing_status import AHEAD_SHOWN, ticket_landing_status
from loregarden.testing.factories import (
    make_artifact,
    make_orchestration_run,
    make_ticket,
    make_workspace,
)
from sqlmodel import Session, select
from tests.worktree_helpers import at_terminal_stage


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    return make_workspace(session, slug="landing-status")


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    ticket = make_ticket(
        session,
        workspace_id=workspace.id,
        external_id="lg-landing-1",
        state=TicketState.IN_PROGRESS,
    )
    at_terminal_stage(session, ticket)
    session.refresh(ticket)
    return ticket


def _lease(session, *, ticket_id=None, label, status, position=0, **fields) -> DockerLease:
    lease = DockerLease(
        holder_kind=DockerHolderKind.AD_HOC,
        holder_label=label,
        ticket_id=ticket_id,
        status=status,
        footprint=DockerFootprint.HEAVY,
        cpus=4.0,
        memory_mb=8192,
        position=position,
        **fields,
    )
    session.add(lease)
    session.commit()
    session.refresh(lease)
    return lease


def _land_label(ticket) -> str:
    return f"gates {LAND_VERIFY_TRANSITION} 0123456789ab · landing-status · {ticket.external_id}"


def _receipt(session, ticket, status, *, outcome, minutes_ago, exit_code=None):
    return make_artifact(
        session,
        ticket_id=ticket.id,
        kind=ArtifactKind.LAND_VERIFICATION,
        title=f"Land verification — {status} — {ticket.branch}",
        content={
            "status": status,
            "stage_key": "done",
            "outcome": outcome,
            "command": "bash .lefthook/scripts/server-tests.sh",
            "exit_code": exit_code,
            "output": "",
        },
        created_at=utcnow() - timedelta(minutes=minutes_ago),
    )


def test_a_ticket_with_nothing_in_flight_is_not_landing(session, workspace, ticket):
    status = ticket_landing_status(session, ticket, workspace)
    assert status.phase is LandingPhase.NOT_LANDING
    assert status.refused_attempts == 0 and status.last_refusal is None


def test_a_run_on_the_terminal_stage_with_no_lease_is_merging(session, workspace, ticket):
    run = make_orchestration_run(session, workspace_id=workspace.id, ticket_id=ticket.id)
    run.status = OrchestrationRunStatus.RUNNING
    run.current_stage_key = ticket.workflow_stage_key
    session.add(run)
    session.commit()

    assert ticket_landing_status(session, ticket, workspace).phase is LandingPhase.MERGING


def test_a_run_on_another_stage_is_not_a_landing(session, workspace, ticket):
    run = make_orchestration_run(session, workspace_id=workspace.id, ticket_id=ticket.id)
    run.status = OrchestrationRunStatus.RUNNING
    run.current_stage_key = "work"
    session.add(run)
    session.commit()

    assert ticket_landing_status(session, ticket, workspace).phase is LandingPhase.NOT_LANDING


def test_a_held_land_lease_is_verifying_with_its_progress_and_limit(session, workspace, ticket):
    granted = utcnow() - timedelta(minutes=12)
    _lease(
        session,
        ticket_id=ticket.id,
        label=_land_label(ticket),
        status=DockerLeaseStatus.HELD,
        granted_at=granted,
        progress_step="pytest on 49 file(s)",
        progress_done=412,
        progress_total=1830,
    )

    status = ticket_landing_status(session, ticket, workspace)

    assert status.phase is LandingPhase.VERIFYING
    assert status.since == granted
    assert status.timeout_seconds == LAND_VERIFY_TIMEOUT_SECONDS
    assert (status.progress_done, status.progress_total) == (412, 1830)
    assert "pytest on 49 file(s)" in status.progress


def test_a_waiting_land_lease_names_who_is_ahead_holders_first(session, workspace, ticket):
    other = make_ticket(session, workspace_id=workspace.id, external_id="lg-other")
    for index in range(AHEAD_SHOWN + 2):
        _lease(
            session,
            ticket_id=other.id,
            label=f"waiter {index}",
            status=DockerLeaseStatus.WAITING,
            position=index + 1,
        )
    _lease(
        session,
        label="pre-push suite",
        status=DockerLeaseStatus.HELD,
        granted_at=utcnow() - timedelta(minutes=3),
    )
    mine = _lease(
        session,
        ticket_id=ticket.id,
        label=_land_label(ticket),
        status=DockerLeaseStatus.WAITING,
        position=AHEAD_SHOWN + 10,
    )
    # A waiter behind mine is not ahead of it.
    _lease(session, label="behind", status=DockerLeaseStatus.WAITING, position=AHEAD_SHOWN + 20)

    status = ticket_landing_status(session, ticket, workspace)

    assert status.phase is LandingPhase.WAITING_FOR_CAPACITY
    assert status.since == as_utc(mine.requested_at)
    assert status.ahead_count == AHEAD_SHOWN + 3
    assert len(status.ahead) == AHEAD_SHOWN
    assert status.ahead[0].label == "pre-push suite" and status.ahead[0].held
    assert [row.label for row in status.ahead[1:]] == [
        f"waiter {i}" for i in range(AHEAD_SHOWN - 1)
    ]


def test_another_tickets_land_lease_and_a_released_one_are_not_this_landing(
    session, workspace, ticket
):
    other = make_ticket(session, workspace_id=workspace.id, external_id="lg-other")
    _lease(session, ticket_id=other.id, label=_land_label(other), status=DockerLeaseStatus.HELD)
    _lease(
        session,
        ticket_id=ticket.id,
        label=_land_label(ticket),
        status=DockerLeaseStatus.RELEASED,
        released_at=utcnow(),
    )
    # A different gate transition on this ticket is not the land verification.
    _lease(
        session,
        ticket_id=ticket.id,
        label=f"gates review→gate · landing-status · {ticket.external_id}",
        status=DockerLeaseStatus.HELD,
    )

    assert ticket_landing_status(session, ticket, workspace).phase is LandingPhase.NOT_LANDING


def test_refusals_count_back_to_the_last_verification_that_was_not_refused(
    session, workspace, ticket
):
    _receipt(session, ticket, RECEIPT_FAILED, outcome="failed", minutes_ago=200, exit_code=1)
    _receipt(session, ticket, RECEIPT_PASSED, outcome="passed", minutes_ago=150, exit_code=0)
    _receipt(session, ticket, RECEIPT_FAILED, outcome="failed", minutes_ago=100, exit_code=1)
    latest = _receipt(session, ticket, RECEIPT_FAILED, outcome="unavailable", minutes_ago=5)

    status = ticket_landing_status(session, ticket, workspace)

    assert status.refused_attempts == 2
    assert status.last_refusal is not None
    assert status.last_refusal.artifact_id == latest.id
    assert status.last_refusal.outcome is GateOutcome.UNAVAILABLE
    assert status.last_refusal.exit_code is None


def test_a_disabled_receipt_ends_the_refusal_streak(session, workspace, ticket):
    _receipt(session, ticket, RECEIPT_FAILED, outcome="failed", minutes_ago=100, exit_code=1)
    _receipt(session, ticket, RECEIPT_DISABLED, outcome="already_contained", minutes_ago=5)

    status = ticket_landing_status(session, ticket, workspace)

    assert status.refused_attempts == 0 and status.last_refusal is None


def test_the_endpoint_answers_by_external_id(client, session):
    workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    make_ticket(session, workspace_id=workspace.id, external_id="lg-landing-api")

    response = client.get("/api/tickets/lg-landing-api/landing")

    assert response.status_code == 200, response.text
    assert response.json()["phase"] == LandingPhase.NOT_LANDING.value
    assert client.get("/api/tickets/lg-no-such-ticket/landing").status_code == 404
