"""An operator stop reaches a detached agent (471).

Before 317, stop worked because the supervising process owned the pipe:
`permission_bridge._check_cancel` polled the flag and killed its own child. A
detached child in its own session is not reachable that way, and after 470 a run
can outlive the process that spawned it — so stop became a flag nobody acts on,
which is the failure #170 fixed for external-harness runs, reintroduced.

These tests spawn real detached processes, because the thing under test is a
signal reaching a process group. A mocked `os.killpg` would assert that this
module calls the function it obviously calls, and would prove nothing about
whether the child actually dies — or, worse, nothing about whether a *stranger*
would.
"""

import os
import signal
import subprocess
import sys
import time

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from loregarden.models.domain.enums import DetachedStopOutcome
from loregarden.services.process_identity import identify
from loregarden.services.run_detached_stop import stop_detached_process
from sqlmodel import Session

_DEAD_PID = 2147483646


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _detached(script: str = "import time; time.sleep(60)") -> subprocess.Popen:
    """A process in its own session, exactly as `cli._spawn_print_process` makes one."""
    return subprocess.Popen([sys.executable, "-c", script], start_new_session=True)


def _run(session, *, pid=None, identity="") -> AgentRun:
    ws = Workspace(slug="wsx", name="WSX", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    ticket = Ticket(external_id=f"t-{os.urandom(3).hex()}", workspace_id=ws.id, title="demo")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        workspace_id=ws.id,
        ticket_id=ticket.id,
        run_code="R1",
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
        agent_pid=pid,
        agent_pid_identity=identity,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _gone(proc: subprocess.Popen, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


def test_a_detached_process_is_actually_killed(session):
    """AC1. The child dies, from a process that did not spawn it as a pipe child."""
    proc = _detached()
    try:
        run = _run(session, pid=proc.pid, identity=identify(proc.pid) or "")

        outcome = stop_detached_process(run)

        assert outcome is DetachedStopOutcome.SIGNALLED
        assert _gone(proc), "the detached process survived the stop"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_the_whole_process_group_goes_not_just_the_leader(session):
    """The reason this signals a group rather than a pid.

    An agent CLI spawns children of its own. Killing only the session leader
    leaves them running and writing, which is the leak detachment was supposed
    to stop rather than create.
    """
    parent = _detached(
        "import subprocess, sys, time; "
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(c.pid, flush=True); time.sleep(60)"
    )
    child_pid = None
    try:
        # The leader prints its child's pid; read it before signalling.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and child_pid is None:
            time.sleep(0.1)
            try:
                child_pid = int(
                    subprocess.run(
                        ["pgrep", "-P", str(parent.pid)],
                        capture_output=True,
                        text=True,
                        check=False,
                    ).stdout.split()[0]
                )
            except (IndexError, ValueError):
                child_pid = None
        assert child_pid, "could not observe the grandchild; test cannot judge"

        run = _run(session, pid=parent.pid, identity=identify(parent.pid) or "")
        stop_detached_process(run)

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if identify(child_pid) is None:
                break
            time.sleep(0.05)
        assert identify(child_pid) is None, "the grandchild outlived the stop"
    finally:
        for pid in (child_pid, parent.pid):
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


def test_a_process_that_is_already_gone_reports_so_and_does_not_error(session):
    """AC3. Stopping a finished run settles as it does today rather than raising."""
    run = _run(session, pid=_DEAD_PID, identity="gone")

    assert stop_detached_process(run) is DetachedStopOutcome.ALREADY_GONE


def test_a_run_with_no_pid_is_already_gone(session):
    run = _run(session, pid=None, identity="")

    assert stop_detached_process(run) is DetachedStopOutcome.ALREADY_GONE


def test_a_reused_pid_is_not_signalled(session):
    """AC2, and the one that matters most.

    A live process whose fingerprint does not match this run is a stranger. The
    blast radius here is a process *group*, so signalling it would take out that
    stranger and its children. Failing to stop is the better failure.
    """
    proc = _detached()
    try:
        run = _run(session, pid=proc.pid, identity="not-the-identity-this-process-has")

        outcome = stop_detached_process(run)

        assert outcome is DetachedStopOutcome.NOT_OURS
        assert proc.poll() is None, "a process that was not ours was signalled"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_a_live_pid_that_is_not_a_session_leader_is_not_signalled(session):
    """The second, independent guard.

    Every agent this control plane spawns is a session leader, so a live pid
    that is not its own process-group leader cannot be one of ours whatever the
    fingerprint says. This is what catches a reused pid whose fingerprint
    happened to be recorded from it.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # not detached
    try:
        assert os.getpgid(proc.pid) != proc.pid, "precondition: this process leads no group"
        run = _run(session, pid=proc.pid, identity=identify(proc.pid) or "")

        outcome = stop_detached_process(run)

        assert outcome is DetachedStopOutcome.NOT_OURS
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_a_process_that_ignores_sigterm_is_killed(session):
    """The grace period ends in SIGKILL, or a stop is only a request."""
    proc = _detached(
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    )
    try:
        run = _run(session, pid=proc.pid, identity=identify(proc.pid) or "")

        outcome = stop_detached_process(run, grace_seconds=0.5)

        assert outcome is DetachedStopOutcome.SIGNALLED
        assert _gone(proc), "a process ignoring SIGTERM was never SIGKILLed"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
