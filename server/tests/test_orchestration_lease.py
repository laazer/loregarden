"""A lane is held by a promise, so the promise has to expire.

`OrchestrationRun` carried no owner, pid, heartbeat or deadline, so
`_occupant_is_live` answered "is this still running?" with `status in
LIVE_ORCHESTRATION_STATUSES` — a field only the run's own owner ever moves. An
external harness lives in someone else's terminal by design; when that session
walked away the status stayed RUNNING and the lane was held permanently. Not
until restart: the startup reaper exempts external runs unconditionally, and the
operator surface its comment delegates cleanup to has never existed.

Observed 2026-08-14 as `orch_5a2daa`: zero agent runs beneath it, slot 1 bound
three seconds after it started and never released, all three slots reading
`is_available = 0` with the pool's real capacity at zero.

The lease is renewed by the work itself — any control-plane write naming the
run — so a slow-but-live session keeps its lane with nobody vouching for it,
and only a session that has stopped talking expires.
"""

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentSlot,
    ExternalHarness,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.run_concurrency import (
    ORCHESTRATION_LEASE,
    orchestration_lease_expired,
)
from loregarden.services.run_service import fail_interrupted_orchestration_runs
from sqlmodel import Session, select

EXPIRED = ORCHESTRATION_LEASE + timedelta(minutes=5)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session: Session, workspace, code: str = "T-1") -> Ticket:
    ticket = Ticket(external_id=code, workspace_id=workspace.id, title=code)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _orch(
    session: Session,
    ticket: Ticket,
    *,
    external: ExternalHarness | None = None,
    age: timedelta = timedelta(minutes=1),
    last_seen: timedelta | None = None,
) -> OrchestrationRun:
    started = datetime.now(timezone.utc) - age
    run = OrchestrationRun(
        run_code="orch_1",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
        started_at=started,
        created_at=started,
        external_harness=external,
        last_seen_at=None if last_seen is None else datetime.now(timezone.utc) - last_seen,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# ---- the lease itself --------------------------------------------------


def test_a_renewed_run_holds_its_lease(session, workspace):
    run = _orch(session, _ticket(session, workspace), age=EXPIRED, last_seen=timedelta(minutes=1))
    assert orchestration_lease_expired(session, run) is False


def test_a_quiet_run_expires(session, workspace):
    run = _orch(session, _ticket(session, workspace), age=EXPIRED, last_seen=EXPIRED)
    assert orchestration_lease_expired(session, run) is True


def test_a_run_never_renewed_falls_back_to_when_it_started(session, workspace):
    """Rows written before the lease existed must be reclaimable without a backfill."""
    assert orchestration_lease_expired(
        session, _orch(session, _ticket(session, workspace), age=EXPIRED)
    )
    assert not orchestration_lease_expired(
        session, _orch(session, _ticket(session, workspace, "T-2"))
    )


def test_a_control_plane_write_renews_the_lease(session, workspace):
    """The work vouches for the run; nobody has to remember to."""
    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, age=EXPIRED)
    assert orchestration_lease_expired(session, run) is True

    OrchestrationCallbackService(session).touch_lease(run)
    session.commit()

    assert orchestration_lease_expired(session, run) is False


# ---- the lane comes back -----------------------------------------------


def test_an_expired_lease_releases_the_lane(session, workspace):
    """The permanent case: status says RUNNING and always will."""
    run = _orch(session, _ticket(session, workspace), external=ExternalHarness.CODEX, age=EXPIRED)
    slot = AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=run.id)
    session.add(slot)
    session.commit()

    freed = ParallelQueueService(session).reconcile_slots()

    assert freed == [1]
    session.refresh(slot)
    assert slot.is_available is True


def test_a_live_external_session_keeps_its_lane(session, workspace):
    """The property the exemption exists to protect.

    An external caller still making control-plane writes must not lose its lane
    to a sweep — that is the whole reason external runs are exempt at all.
    """
    run = _orch(
        session,
        _ticket(session, workspace),
        external=ExternalHarness.CODEX,
        age=EXPIRED,
        last_seen=timedelta(minutes=2),
    )
    slot = AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=run.id)
    session.add(slot)
    session.commit()

    assert ParallelQueueService(session).reconcile_slots() == []
    session.refresh(slot)
    assert slot.is_available is False


# ---- the startup reaper's exemption is now bounded ---------------------


def test_the_reaper_still_spares_a_live_external_run_across_a_restart(session, workspace):
    """Restarting this server must not end someone's terminal session."""
    run = _orch(
        session,
        _ticket(session, workspace),
        external=ExternalHarness.CODEX,
        last_seen=timedelta(minutes=1),
    )

    assert fail_interrupted_orchestration_runs(session) == []
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.RUNNING


def test_the_reaper_settles_an_abandoned_external_run(session, workspace):
    """Was exempt unconditionally, with cleanup delegated to a surface that
    does not exist — so it was never settled at all."""
    run = _orch(session, _ticket(session, workspace), external=ExternalHarness.CODEX, age=EXPIRED)

    assert [r.id for r in fail_interrupted_orchestration_runs(session)] == [run.id]
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.FAILED


def test_the_reaper_still_settles_a_run_this_server_owned(session, workspace):
    run = _orch(session, _ticket(session, workspace))

    assert [r.id for r in fail_interrupted_orchestration_runs(session)] == [run.id]


# ---- stop is no longer a silent no-op ----------------------------------


def test_stopping_an_external_run_settles_it_rather_than_reporting_success(client, db_session):
    """`cancel_requested_at` is polled by BuiltinOrchestrator, which does not
    drive an external run. Stop set a flag nobody reads, returned 200, and left
    the lane held."""
    ws = db_session.exec(select(Workspace)).first()
    ticket = Ticket(external_id="T-stop", workspace_id=ws.id, title="stop me")
    db_session.add(ticket)
    db_session.commit()
    run = OrchestrationRun(
        run_code="orch_ext",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        status=OrchestrationRunStatus.RUNNING,
        external_harness=ExternalHarness.CODEX,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    assert client.post(f"/api/tickets/{ticket.id}/stop").status_code == 200

    db_session.refresh(run)
    assert run.status is OrchestrationRunStatus.CANCELLED
    assert run.finished_at is not None


# ---- a long stage is not a dead run ------------------------------------


def test_a_long_running_stage_keeps_its_lane(session, workspace):
    """The lease renews at stage boundaries, so a long stage has none to give.

    Two agent runs on the day this was written took 51 and 38 minutes against a
    30-minute lease. Judging on the lease alone frees the lane out from under an
    agent that is working, and the pool then admits past its own limit. An agent
    run in flight is work in flight and outranks the lease.
    """
    from loregarden.models.domain import AgentRun, RunStatus

    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, age=EXPIRED, last_seen=EXPIRED)
    session.add(
        AgentRun(
            run_code="run_long",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="backend_implementer",
            status=RunStatus.RUNNING,
            orchestration_run_id=run.id,
            started_at=datetime.now(timezone.utc) - EXPIRED,
        )
    )
    session.commit()

    assert orchestration_lease_expired(session, run) is False

    slot = AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=run.id)
    session.add(slot)
    session.commit()
    assert ParallelQueueService(session).reconcile_slots() == []
    session.refresh(slot)
    assert slot.is_available is False


# ---- the run becomes terminal, not just the slot free ------------------


def test_an_expired_lease_settles_the_run_not_only_the_slot(session, workspace):
    """The lane coming back is not enough.

    `claim_orchestration_run` adopts any active run for a ticket, so a run left
    RUNNING blocks that ticket from ever being orchestrated again until a
    restart; `ticket_activity` also counts it as running. The lease frees the
    slot on a status read but only the startup reaper settled the run.
    """
    from loregarden.services.run_service import settle_expired_orchestration_leases

    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, external=ExternalHarness.CODEX, age=EXPIRED)

    assert [r.id for r in settle_expired_orchestration_leases(session)] == [run.id]
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.FAILED
    assert run.finished_at is not None


def test_the_sweep_spares_a_renewed_run(session, workspace):
    from loregarden.services.run_service import settle_expired_orchestration_leases

    _orch(session, _ticket(session, workspace), last_seen=timedelta(minutes=1))
    assert settle_expired_orchestration_leases(session) == []


def test_the_sweep_spares_a_run_with_a_live_agent(session, workspace):
    """The long-stage case again, through the sweep rather than the slot check."""
    from loregarden.models.domain import AgentRun, RunStatus
    from loregarden.services.run_service import settle_expired_orchestration_leases

    ticket = _ticket(session, workspace)
    run = _orch(session, ticket, age=EXPIRED, last_seen=EXPIRED)
    session.add(
        AgentRun(
            run_code="run_long",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="backend_implementer",
            status=RunStatus.RUNNING,
            orchestration_run_id=run.id,
        )
    )
    session.commit()

    assert settle_expired_orchestration_leases(session) == []
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.RUNNING


def test_the_status_read_settles_expired_leases(session, workspace):
    """No periodic tick exists; the board read is the cadence."""
    import asyncio

    from loregarden.services.queue_status import build_queue_status

    run = _orch(session, _ticket(session, workspace), external=ExternalHarness.CODEX, age=EXPIRED)
    slot = AgentSlot(slot_number=1, is_available=False, current_orchestration_run_id=run.id)
    session.add(slot)
    session.commit()

    asyncio.run(build_queue_status(session))

    session.refresh(run)
    assert run.status is OrchestrationRunStatus.FAILED
    session.refresh(slot)
    assert slot.is_available is True
