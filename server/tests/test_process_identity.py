"""A pid the control plane can trust, and a process it cannot signal by accident.

317 lets an agent run outlive the server that spawned it. The moment that is
true, "is pid 4821 alive?" stops being a useful question: pids are recycled, and
on a busy machine the number that named an agent an hour ago may name a shell, a
test runner, or nothing.

The two directions are not symmetric. A wrong "orphaned" costs one re-run. A
wrong "still mine" means the control plane adopts, reports on, and eventually
signals a process it does not own — so everything here fails toward orphaned.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, WorkItemType
from loregarden.services.process_identity import (
    identify,
    record_process_identity,
    still_running,
)
from loregarden.services.ticket_service import TicketService


@pytest.fixture(name="ticket")
def ticket_fixture(db_session) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="process identity",
        work_item_type=WorkItemType.MILESTONE,
    )


@pytest.fixture(name="run")
def run_fixture(db_session, ticket) -> AgentRun:
    run = AgentRun(
        run_code="rc_pid",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


# ---- the fingerprint ----------------------------------------------------


def test_a_live_process_has_an_identity():
    """This interpreter is the most convenient live process available."""
    assert identify(os.getpid())


def test_the_same_process_fingerprints_the_same_twice():
    """Otherwise every comparison would read as pid reuse."""
    assert identify(os.getpid()) == identify(os.getpid())


def test_a_dead_process_has_no_identity():
    """A pid that is nobody answers None, not a stale value."""
    assert identify(2147483000) is None


def test_two_processes_do_not_share_a_fingerprint():
    """The property the whole module rests on: start time separates them."""
    child = subprocess.Popen(["sleep", "30"])
    try:
        assert identify(child.pid) != identify(os.getpid())
    finally:
        child.kill()
        child.wait()


# ---- the comparison, and which way it fails -----------------------------


def test_a_matching_identity_is_still_running():
    assert still_running(os.getpid(), identify(os.getpid())) is True


def test_a_reused_pid_is_not_still_running():
    """The case this exists for.

    A live pid whose fingerprint does not match is a *different* process wearing
    a familiar number. Adopting it would be the control plane reporting on, and
    later signalling, something it does not own.
    """
    assert still_running(os.getpid(), "Thu Jan  1 00:00:00 1970") is False


def test_a_run_with_no_recorded_identity_is_not_adopted(db_session):
    """Fails closed, deliberately.

    A run from before identity was recorded has a pid nobody can vouch for.
    Falling back to a bare liveness check there is exactly the adoption mistake
    this module prevents, so absence of identity means "do not adopt".
    """
    assert still_running(os.getpid(), "") is False
    assert still_running(os.getpid(), None) is False
    assert still_running(None, "anything") is False


def test_an_unreadable_identity_is_not_still_running():
    """`ps` failing is not evidence the process is ours."""
    with patch("loregarden.services.process_identity.identify", return_value=None):
        assert still_running(os.getpid(), "something") is False


# ---- recorded on the run ------------------------------------------------


def test_the_run_records_both_the_pid_and_its_identity(db_session, run):
    """A pid stored alone is a number a later process can wear."""
    record_process_identity(run.id, os.getpid())

    db_session.expire_all()
    stored = db_session.get(AgentRun, run.id)
    assert stored.agent_pid == os.getpid()
    assert stored.agent_pid_identity
    assert still_running(stored.agent_pid, stored.agent_pid_identity) is True


def test_recording_never_fails_the_run(db_session, run):
    """Bookkeeping must not take down the work it is describing."""
    with patch("loregarden.services.process_identity.Session", side_effect=RuntimeError("db gone")):
        record_process_identity(run.id, os.getpid())  # must not raise


def test_recording_against_a_missing_run_is_a_no_op(db_session):
    record_process_identity("no-such-run", os.getpid())  # must not raise
