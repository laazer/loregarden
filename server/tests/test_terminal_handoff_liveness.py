"""Terminal-handoff runs must be reapable when provably dead.

A handoff AgentRun is created RUNNING before any process exists (the human still
has to paste the command), so nothing supervises it. Left unreaped, a phantom
handoff run blocks triage chat and the self-improve restart watcher forever.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import RunStatus, StageStatus, Ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_service import (
    HANDOFF_EXITED_MESSAGE,
    STALE_HANDOFF_NEVER_STARTED_MESSAGE,
    STALE_HANDOFF_SHELL_DIED_MESSAGE,
    TERMINAL_HANDOFF_COMMAND_PREFIX,
    fail_stale_handoff_runs,
)
from loregarden.services.seed import seed_database
from loregarden.services.triage_run_service import TriageConflictError, start_triage_run
from sqlmodel import Session, select

STALE_AGE = timedelta(minutes=30)  # comfortably past the 15-minute check-in grace


def _ticket(session: Session) -> Ticket:
    return session.exec(
        select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
    ).first()


def _handoff_run(session: Session, ticket: Ticket, *, age: timedelta | None = None):
    run = OrchestrationService(session).start_run(ticket, stage_key="testing")
    run.command = f"{TERMINAL_HANDOFF_COMMAND_PREFIX} claude --add-dir /tmp/nowhere"
    if age is not None:
        run.started_at = datetime.now(timezone.utc) - age
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_never_checked_in_handoff_is_reaped_after_grace(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        run = _handoff_run(session, ticket, age=STALE_AGE)

        reaped = fail_stale_handoff_runs(session, ticket_id=ticket.id)

        assert [r.id for r in reaped] == [run.id]
        session.refresh(run)
        assert run.status == RunStatus.FAILED
        assert STALE_HANDOFF_NEVER_STARTED_MESSAGE in run.stderr
        session.refresh(ticket)
        assert ticket.workflow_stage_status == StageStatus.BLOCKED


def test_fresh_handoff_within_grace_is_left_alone(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        run = _handoff_run(session, ticket)

        assert fail_stale_handoff_runs(session, ticket_id=ticket.id) == []
        session.refresh(run)
        assert run.status == RunStatus.RUNNING


def test_checked_in_handoff_with_live_shell_is_left_alone(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        run = _handoff_run(session, ticket, age=STALE_AGE)
        run.handoff_accepted_at = datetime.now(timezone.utc)
        run.handoff_pid = os.getpid()
        session.add(run)
        session.commit()

        assert fail_stale_handoff_runs(session, ticket_id=ticket.id) == []
        session.refresh(run)
        assert run.status == RunStatus.RUNNING


def test_checked_in_handoff_with_dead_shell_is_reaped(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        run = _handoff_run(session, ticket)
        run.handoff_accepted_at = datetime.now(timezone.utc)
        run.handoff_pid = os.getpid()
        session.add(run)
        session.commit()

        with patch("loregarden.services.run_service._pid_alive", return_value=False):
            reaped = fail_stale_handoff_runs(session, ticket_id=ticket.id)

        assert [r.id for r in reaped] == [run.id]
        session.refresh(run)
        assert run.status == RunStatus.FAILED
        assert STALE_HANDOFF_SHELL_DIED_MESSAGE in run.stderr


def test_non_handoff_runs_are_never_reaped(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        run = OrchestrationService(session).start_run(ticket, stage_key="testing")
        run.started_at = datetime.now(timezone.utc) - STALE_AGE
        session.add(run)
        session.commit()

        assert fail_stale_handoff_runs(session, ticket_id=ticket.id) == []
        session.refresh(run)
        assert run.status == RunStatus.RUNNING


def test_reap_skips_workflow_advance_when_ticket_moved_on(isolated_db):
    """A human may advance the workflow while the phantom run sits RUNNING —
    settling it later must not re-mark the moved-on stage BLOCKED. A handoff
    that never even started earns no success from the stage moving on."""
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        run = _handoff_run(session, ticket, age=STALE_AGE)
        ticket.workflow_stage_key = "review"
        stage_status_before = ticket.workflow_stage_status
        session.add(ticket)
        session.commit()

        reaped = fail_stale_handoff_runs(session, ticket_id=ticket.id)

        assert [r.id for r in reaped] == [run.id]
        session.refresh(run)
        assert run.status == RunStatus.FAILED
        session.refresh(ticket)
        assert ticket.workflow_stage_key == "review"
        assert ticket.workflow_stage_status == stage_status_before


def test_dead_shell_after_stage_progressed_settles_as_success(isolated_db):
    """Terminal session completes its stage (leaving a gate AWAITING), the user
    closes the tab later: the run succeeded, and the AWAITING gate must not be
    re-marked BLOCKED by the reaper."""
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        run = _handoff_run(session, ticket)
        run.handoff_accepted_at = datetime.now(timezone.utc)
        run.handoff_pid = os.getpid()
        session.add(run)
        ticket.workflow_stage_status = StageStatus.AWAITING
        session.add(ticket)
        session.commit()

        with patch("loregarden.services.run_service._pid_alive", return_value=False):
            reaped = fail_stale_handoff_runs(session, ticket_id=ticket.id)

        assert [r.id for r in reaped] == [run.id]
        session.refresh(run)
        assert run.status == RunStatus.SUCCEEDED
        session.refresh(ticket)
        assert ticket.workflow_stage_status == StageStatus.AWAITING


def test_triage_turn_reaps_stale_handoff_instead_of_conflicting(isolated_db):
    """The bug this module exists for: a never-pasted handoff must not lock the
    operator out of triage chat with 'Another run is already active'."""
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        stale = _handoff_run(session, ticket, age=STALE_AGE)

        message, turn = start_triage_run(session, ticket, "hello?")

        assert message.content == "hello?"
        assert turn.status == RunStatus.QUEUED
        session.refresh(stale)
        assert stale.status == RunStatus.FAILED


def test_triage_turn_still_conflicts_with_live_handoff(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        _handoff_run(session, ticket)

        with pytest.raises(TriageConflictError):
            start_triage_run(session, ticket, "hello?")


def test_handoff_checkin_records_shell_pid(client: TestClient, db_session: Session):
    ticket = _ticket(db_session)
    run = _handoff_run(db_session, ticket)

    res = client.post(f"/api/runs/{run.id}/handoff-checkin", json={"pid": 4242})

    assert res.status_code == 200
    db_session.refresh(run)
    assert run.handoff_pid == 4242
    assert run.handoff_accepted_at is not None


def test_handoff_checkin_rejects_settled_run(client: TestClient, db_session: Session):
    """The pasted command chains with `&&` — this 409 is what stops the CLI from
    doing stage work against a run that was already reaped."""
    ticket = _ticket(db_session)
    run = _handoff_run(db_session, ticket, age=STALE_AGE)
    fail_stale_handoff_runs(db_session, ticket_id=ticket.id)

    res = client.post(f"/api/runs/{run.id}/handoff-checkin", json={"pid": 4242})

    assert res.status_code == 409


def test_handoff_checkin_rejects_non_handoff_run(client: TestClient, db_session: Session):
    ticket = _ticket(db_session)
    run = OrchestrationService(db_session).start_run(ticket, stage_key="testing")

    res = client.post(f"/api/runs/{run.id}/handoff-checkin", json={"pid": 4242})

    assert res.status_code == 409


def test_handoff_exited_settles_incomplete_run(client: TestClient, db_session: Session):
    ticket = _ticket(db_session)
    run = _handoff_run(db_session, ticket)

    res = client.post(f"/api/runs/{run.id}/handoff-exited")

    assert res.status_code == 200
    assert res.json()["status"] == RunStatus.FAILED.value
    db_session.refresh(run)
    assert HANDOFF_EXITED_MESSAGE in run.stderr


def test_handoff_exited_is_noop_for_settled_run(client: TestClient, db_session: Session):
    ticket = _ticket(db_session)
    run = _handoff_run(db_session, ticket)
    OrchestrationService(db_session).complete_run(run, status=RunStatus.SUCCEEDED)

    res = client.post(f"/api/runs/{run.id}/handoff-exited")

    assert res.status_code == 200
    assert res.json()["status"] == RunStatus.SUCCEEDED.value


def test_rendered_handoff_command_brackets_cli_with_liveness_pings():
    from pathlib import Path

    from loregarden.agents.cli_adapters import CliInvocation, render_terminal_handoff_command

    invocation = CliInvocation(
        argv=["claude", "--add-dir", "/tmp/repo"],
        use_prompt_file=True,
        adapter="claude",
        cwd="/tmp/repo",
    )
    command = render_terminal_handoff_command(
        invocation, cleanup_path=Path("/tmp/loregarden-handoff-x"), run_id="run-1"
    )

    checkin = command.index("/api/runs/run-1/handoff-checkin")
    cli = command.index("claude")
    exited = command.index("/api/runs/run-1/handoff-exited")
    assert checkin < cli < exited
    # The CLI must not start against a reaped run: check-in gates it with `&&`.
    assert "handoff-checkin > /dev/null && " in command
    # The shell's own pid rides along for the liveness reaper.
    assert "$$" in command
