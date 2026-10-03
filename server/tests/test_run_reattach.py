"""A run whose agent outlived the restart is adopted, not declared orphaned (470).

317 detaches the agent subprocess so a restart stops killing live turns. Without
this, detaching only leaks processes: the agent keeps working and the next boot
marks its run interrupted anyway, so the work continues with nothing recording it.

`fail_interrupted_runs` fails every in-flight run with no liveness test — sound
only while "in flight at startup" was provably a lie. Detachment makes it false.

The identity check is the point, not the liveness check. A pid alone is not
evidence: pids are recycled, and adopting a stranger is worse than declaring the
run dead. A wrong "orphaned" costs one re-run; a wrong "still mine" means the
control plane reports on, and eventually signals, a process it does not own.
"""

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from loregarden.models.domain import AgentRun, ProcessState, RunStatus, Ticket, Workspace
from loregarden.models.domain.process_enums import DetachedStopOutcome
from loregarden.services import process_identity, run_reattach
from loregarden.services.process_identity import identify
from loregarden.services.run_detached_stop import stop_detached_process
from loregarden.services.run_lease import agent_run_lease_expired
from loregarden.services.run_reattach import reattach_surviving_runs, surviving_runs
from loregarden.services.run_service import fail_interrupted_runs
from sqlmodel import Session

#: A pid no process on a sane machine holds, used where the point is that the
#: process is gone rather than which process it was.
_DEAD_PID = 2147483646


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="sleeper")
def sleeper_fixture():
    """A real live process to identify, killed when the test ends."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait(timeout=5)


def _run(session, *, pid=None, identity="", harness=None, status=RunStatus.RUNNING) -> AgentRun:
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
        status=status,
        agent_pid=pid,
        agent_pid_identity=identity,
        external_harness=harness,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_live_process_with_a_matching_identity_survives(session, sleeper):
    """AC1. The whole feature in one assertion."""
    run = _run(session, pid=sleeper.pid, identity=identify(sleeper.pid).stamp)

    assert [r.id for r in surviving_runs(session)] == [run.id]


def test_a_dead_pid_does_not_survive(session):
    """AC2. The existing interruption path still owns a genuinely orphaned run."""
    _run(session, pid=_DEAD_PID, identity="whatever")

    assert surviving_runs(session) == []


def test_a_live_pid_with_a_mismatched_identity_does_not_survive(session, sleeper):
    """AC2's sharp edge, and the reason a bare liveness check is not enough.

    Pids are recycled. This is a live process that is *not* the one the run
    recorded — adopting it would have the control plane reporting on, and later
    signalling, a stranger.
    """
    _run(session, pid=sleeper.pid, identity="not-the-identity-this-process-has")

    assert surviving_runs(session) == []


def test_a_run_with_no_recorded_identity_does_not_survive(session, sleeper):
    """Fails closed, inherited from `still_running`.

    A run written before identity was recorded has a pid nobody can vouch for.
    Treating it as alive is exactly the adoption mistake.
    """
    _run(session, pid=sleeper.pid, identity="")

    assert surviving_runs(session) == []


def test_an_externally_harnessed_run_is_left_alone(session, sleeper):
    """Its process is on someone else's machine; there is no pid here to identify."""
    from loregarden.models.domain import ExternalHarness

    _run(
        session,
        pid=sleeper.pid,
        identity=identify(sleeper.pid).stamp,
        harness=ExternalHarness.CLAUDE_CODE,
    )

    assert surviving_runs(session) == []


def test_the_boot_reaper_does_not_fail_a_reattached_run(session, sleeper):
    """AC5's real point: the two halves have to agree.

    A reattach step that adopted a run the reaper then failed would be worse
    than no reattach at all — the row would be marked interrupted while a live
    agent kept writing to it.
    """
    run = _run(session, pid=sleeper.pid, identity=identify(sleeper.pid).stamp)

    reattach_surviving_runs(session, interval_seconds=0.05)
    failed = fail_interrupted_runs(session)

    assert run.id not in {r.id for r in failed}
    session.refresh(run)
    assert run.status == RunStatus.RUNNING


def test_the_boot_reaper_still_fails_a_dead_run(session):
    """The control. Without it, breaking the reaper entirely would pass the test above."""
    run = _run(session, pid=_DEAD_PID, identity="gone")

    reattach_surviving_runs(session, interval_seconds=0.05)
    failed = fail_interrupted_runs(session)

    assert run.id in {r.id for r in failed}


def test_reattaching_renews_the_lease(session, sleeper):
    """AC3. Without a renewer the adopted run is reaped at the lease boundary."""
    run = _run(session, pid=sleeper.pid, identity=identify(sleeper.pid).stamp)
    assert run.last_seen_at is None

    reattach_surviving_runs(session, interval_seconds=0.05)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        session.refresh(run)
        if run.last_seen_at is not None:
            break
        time.sleep(0.05)
    assert run.last_seen_at is not None, "the adopted run's lease was never renewed"


def test_renewal_stops_when_the_reattached_process_exits(session):
    """AC4, and the half a naive renewer misses.

    An unconditional renewer would hold a run alive forever after its agent
    died — a run the control plane can no longer see, reported as fine. When the
    process goes, renewal has to stop so the lease sweep can settle the row.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    run = _run(session, pid=proc.pid, identity=identify(proc.pid).stamp)

    reattach_surviving_runs(session, interval_seconds=0.05)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        session.refresh(run)
        if run.last_seen_at is not None:
            break
        time.sleep(0.05)
    assert run.last_seen_at is not None, "precondition: renewal never started"

    proc.kill()
    proc.wait(timeout=5)
    time.sleep(0.4)  # let the watcher observe the exit and leave its loop
    session.refresh(run)
    settled = run.last_seen_at

    time.sleep(0.4)
    session.refresh(run)
    assert run.last_seen_at == settled, "renewal continued after the process exited"


# --- lg-run-durability-862: a `ps` that cannot answer is not a death ---------


def _ps_times_out(args, *rest, **kwargs):
    raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 5))


def test_a_live_run_survives_a_ps_that_cannot_answer(session, sleeper):
    """Moved from test_open_defect_repros.py, where it was 862's repro.

    The run is recorded with its real identity; then every `ps` at boot times
    out. Failing the run would be the reaper killing a working agent's row.
    """
    run = _run(session, pid=sleeper.pid, identity=identify(sleeper.pid).stamp)

    with mock.patch.object(process_identity.subprocess, "run", _ps_times_out):
        reattach_surviving_runs(session, interval_seconds=0.05)
        failed = fail_interrupted_runs(session)

    assert run.id not in {r.id for r in failed}


def test_a_run_ps_cannot_answer_about_is_not_adopted_either(session, sleeper, caplog):
    """Neither adopted nor failed: nothing vouches for the process, so no lease
    renewal starts; the lease reaper asks again on its next sweep."""
    run = _run(session, pid=sleeper.pid, identity=identify(sleeper.pid).stamp)

    with (
        mock.patch.object(process_identity.subprocess, "run", _ps_times_out),
        caplog.at_level(logging.WARNING, logger="loregarden.services.run_reattach"),
    ):
        adopted = reattach_surviving_runs(session, interval_seconds=0.05)
        fail_interrupted_runs(session)

    assert run.id not in {r.id for r in adopted}
    assert any(str(sleeper.pid) in r.getMessage() for r in caplog.records)
    session.refresh(run)
    assert run.status is RunStatus.RUNNING


def test_the_watch_skips_a_beat_on_unknown_and_stops_only_on_gone(session):
    """A transient `ps` failure mid-watch must not end renewal for good."""
    readings = iter(
        [ProcessState.ALIVE, ProcessState.UNKNOWN, ProcessState.ALIVE, ProcessState.GONE]
    )
    with (
        mock.patch.object(run_reattach, "liveness", side_effect=lambda *_: next(readings)),
        mock.patch.object(run_reattach, "renew_agent_run_lease") as renew,
    ):
        run_reattach._watch("run-id", 123, "identity", 0)

    assert renew.call_count == 2


def test_the_lease_reaper_does_not_take_unknown_as_alive(session, sleeper):
    """Left for the lease: a `ps` that cannot answer vouches for nothing, so an
    expired lease is expired. A process `ps` *can* see stays alive whatever the
    lease says."""
    run = _run(session, pid=sleeper.pid, identity=identify(sleeper.pid).stamp)
    run.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=1)
    session.add(run)
    session.commit()

    assert agent_run_lease_expired(session, run, lease=timedelta(minutes=10)) is False
    with mock.patch.object(process_identity.subprocess, "run", _ps_times_out):
        assert agent_run_lease_expired(session, run, lease=timedelta(minutes=10)) is True


def test_a_stop_cannot_vouch_for_a_pid_ps_cannot_answer_about(session, sleeper):
    """Not ALREADY_GONE — the run did not finish — and nothing is signalled."""
    run = _run(session, pid=sleeper.pid, identity=identify(sleeper.pid).stamp)

    with mock.patch.object(process_identity.subprocess, "run", _ps_times_out):
        outcome = stop_detached_process(run, grace_seconds=0.1)

    assert outcome is DetachedStopOutcome.NOT_OURS
    assert sleeper.poll() is None, "nothing may have been signalled"
