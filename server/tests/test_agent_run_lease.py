"""`RUNNING` becomes something that can be disproved.

A run is committed `RUNNING` before its subprocess exists, and if the thread
supervising it dies the row reads `RUNNING` forever with nothing left to notice.
Because the status could not be disproved, `fail_interrupted_runs` never tried —
it fails *every* in-flight run with no liveness test, which is sound only at
startup, where "in flight" is provably a lie. That is why the whole
reconciliation layer was startup-only, and why 437's timer still skips the
reapers.

The rule these pin is narrow and the failures around it are expensive in both
directions: reap a live run and an agent's work is orphaned mid-stage; spare a
dead one and its lane is held forever.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    RunStatus,
    Ticket,
    WorkItemType,
)
from loregarden.services.run_lease import (
    AGENT_RUN_LEASE,
    agent_run_lease_expired,
    lease_renewal,
    renew_agent_run_lease,
    run_has_renewer,
)
from loregarden.services.run_service import settle_expired_agent_runs
from loregarden.services.ticket_service import TicketService

EXPIRED = AGENT_RUN_LEASE + timedelta(minutes=5)


@pytest.fixture(name="ticket")
def ticket_fixture(db_session) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="agent run lease",
        work_item_type=WorkItemType.MILESTONE,
    )


def _run(
    db_session,
    ticket: Ticket,
    *,
    code: str = "run_lease",
    status: RunStatus = RunStatus.RUNNING,
    age: timedelta | None = None,
    last_seen: timedelta | None = None,
    external: ExternalHarness | None = None,
    handoff_pid: int | None = None,
) -> AgentRun:
    now = datetime.now(timezone.utc)
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=status,
        external_harness=external,
        handoff_pid=handoff_pid,
    )
    run.started_at = now - (age or timedelta(minutes=1))
    if last_seen is not None:
        run.last_seen_at = now - last_seen
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


# ---- the predicate ------------------------------------------------------


def test_a_renewing_run_is_alive(db_session, ticket):
    """Old, and still supervised. Age alone is not evidence of anything."""
    run = _run(db_session, ticket, age=timedelta(hours=4), last_seen=timedelta(seconds=20))

    assert agent_run_lease_expired(db_session, run) is False


def test_a_run_nobody_renewed_is_dead(db_session, ticket):
    run = _run(db_session, ticket, age=EXPIRED, last_seen=EXPIRED)

    assert agent_run_lease_expired(db_session, run) is True


def test_a_run_that_was_never_renewed_falls_back_to_its_start(db_session, ticket):
    """No backfill: a row written before the lease existed is still judged."""
    run = _run(db_session, ticket, age=EXPIRED)
    assert run.last_seen_at is None

    assert agent_run_lease_expired(db_session, run) is True


def test_a_finished_run_is_not_the_predicates_business(db_session, ticket):
    run = _run(db_session, ticket, status=RunStatus.SUCCEEDED, age=EXPIRED, last_seen=EXPIRED)

    assert agent_run_lease_expired(db_session, run) is False


def test_a_dead_recorded_pid_settles_it_without_waiting(db_session, ticket):
    """A handoff stamps the shell it was pasted into; a gone pid needs no lease."""
    run = _run(db_session, ticket, age=timedelta(seconds=5), handoff_pid=2147483000)

    assert agent_run_lease_expired(db_session, run) is True


def test_a_live_recorded_pid_outranks_a_stale_lease(db_session, ticket):
    """This process is alive, so the run it stands for is."""
    import os

    run = _run(db_session, ticket, age=EXPIRED, last_seen=EXPIRED, handoff_pid=os.getpid())

    assert agent_run_lease_expired(db_session, run) is False


# ---- fail closed --------------------------------------------------------


def test_an_external_harness_run_is_never_reaped_by_the_lease(db_session, ticket):
    """The regression that closed PR #159, pinned.

    That PR inferred liveness from observed `agent_runs` activity, which an
    externally-harnessed stage never produces — so a live external run looked
    dead. The harness talks to the control plane at stage boundaries, which is a
    report and not a heartbeat, so it has no renewer to judge it by and must
    read as alive.
    """
    run = _run(db_session, ticket, age=EXPIRED, last_seen=EXPIRED, external=ExternalHarness.CODEX)

    assert run_has_renewer(run) is False
    assert agent_run_lease_expired(db_session, run) is False
    assert settle_expired_agent_runs(db_session) == []


def test_a_queued_run_is_not_judged(db_session, ticket):
    """A queued run has no supervisor yet, and may wait a long time for a slot.

    `executor.execute` has not been entered, so nothing is renewing. Judging it
    would reap precisely the runs waiting their turn.
    """
    run = _run(db_session, ticket, status=RunStatus.QUEUED, age=EXPIRED)

    assert agent_run_lease_expired(db_session, run) is False


# ---- the reap -----------------------------------------------------------


def test_the_sweep_fails_a_dead_run_and_spares_a_live_one(db_session, ticket):
    """Both directions in one pass, which is the property that matters.

    A sweep that only ever spared would pass every test above and be useless; a
    sweep that only ever reaped would pass the dead-run test and destroy work.
    """
    dead = _run(db_session, ticket, code="run_dead", age=EXPIRED, last_seen=EXPIRED)
    alive = _run(db_session, ticket, code="run_alive", age=EXPIRED, last_seen=timedelta(seconds=5))

    settled = settle_expired_agent_runs(db_session)

    assert [r.id for r in settled] == [dead.id]
    db_session.expire_all()
    assert db_session.get(AgentRun, dead.id).status is RunStatus.FAILED
    assert db_session.get(AgentRun, alive.id).status is RunStatus.RUNNING


# ---- renewal ------------------------------------------------------------


def test_renewal_stamps_the_run(db_session, ticket):
    run = _run(db_session, ticket, age=EXPIRED)
    assert run.last_seen_at is None

    renew_agent_run_lease(run.id)

    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).last_seen_at is not None


def test_renewal_ignores_a_finished_run(db_session, ticket):
    """Nothing supervises a run that is over; stamping it would fake a lease."""
    run = _run(db_session, ticket, status=RunStatus.SUCCEEDED)

    renew_agent_run_lease(run.id)

    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).last_seen_at is None


def test_the_renewer_runs_for_the_life_of_the_body(db_session, ticket):
    """What wraps `executor.execute`, so every adapter is covered by one seam."""
    run = _run(db_session, ticket, age=EXPIRED)

    stamped = None
    with lease_renewal(run.id, interval_seconds=0.05):
        # The renewer is a thread, so the stamp lands when it lands. Waited for
        # rather than read once: an immediate read races the first beat and
        # would fail for a renewer that works.
        deadline = time.time() + 5
        while stamped is None and time.time() < deadline:
            db_session.expire_all()
            stamped = db_session.get(AgentRun, run.id).last_seen_at
            if stamped is None:
                time.sleep(0.05)

    assert stamped is not None, "the lease was not renewed while the body ran"
    assert agent_run_lease_expired(db_session, db_session.get(AgentRun, run.id)) is False
