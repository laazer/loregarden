"""An orchestration whose driver died must give its lane back.

`fail_interrupted_orchestration_runs` reaps what a *restart* orphaned and runs
only in the startup lifespan. Nothing reaped a run whose thread died while the
server stayed up, and nothing could: `reconcile_slots` frees a slot whose
occupant is terminal, and a run stuck at RUNNING is live by that test. The lane
was therefore held until the next boot — the board reported it busy, and work
queued behind it never started. Two of three lanes were in that state on the
live board when this was written.

The dangerous half of the fix is the false positive. A parent orchestration
runs no stages of its own; the work lives in its children's separate runs. A
liveness test scoped to one run's own ticket reads a working parent as idle and
kills the tree from the top — including, in the incident, a 51-minute agent run
that went on to succeed.
"""

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.run_service import (
    STALLED_ORCHESTRATION_GRACE,
    settle_stalled_orchestrations,
)
from sqlmodel import Session, select


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


def _ticket(session: Session, workspace, code: str, *, parent: Ticket | None = None) -> Ticket:
    ticket = Ticket(
        external_id=code,
        workspace_id=workspace.id,
        title=code,
        parent_ticket_id=parent.id if parent else None,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _orch(
    session: Session,
    ticket: Ticket,
    code: str,
    *,
    age: timedelta,
    status: OrchestrationRunStatus = OrchestrationRunStatus.RUNNING,
) -> OrchestrationRun:
    started = datetime.now(timezone.utc) - age
    run = OrchestrationRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=status,
        started_at=started,
        created_at=started,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _agent_run(
    session: Session,
    ticket: Ticket,
    code: str,
    *,
    status: RunStatus,
    age: timedelta,
    orchestration: OrchestrationRun | None = None,
) -> AgentRun:
    stamp = datetime.now(timezone.utc) - age
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="implementer",
        status=status,
        started_at=stamp,
        created_at=stamp,
        finished_at=None if status is RunStatus.RUNNING else stamp,
        orchestration_run_id=orchestration.id if orchestration else None,
    )
    session.add(run)
    session.commit()
    return run


STALE = STALLED_ORCHESTRATION_GRACE + timedelta(minutes=5)


# ---- the false positive that would cost real work ----------------------


def test_a_parent_is_spared_while_its_child_has_a_live_agent(session, workspace):
    """The incident case: 326 held a lane, 328 underneath it was working.

    The parent's own orchestration had zero agent runs and had been open far
    longer than the grace. Judging it on its own ticket would have killed a
    51-minute agent run that went on to succeed.
    """
    parent = _ticket(session, workspace, "F-1")
    child = _ticket(session, workspace, "T-1", parent=parent)
    parent_run = _orch(session, parent, "orch_parent", age=STALE)
    child_run = _orch(session, child, "orch_child", age=STALE)
    _agent_run(
        session, child, "run_live", status=RunStatus.RUNNING, age=STALE, orchestration=child_run
    )

    assert settle_stalled_orchestrations(session) == []

    session.refresh(parent_run)
    session.refresh(child_run)
    assert parent_run.status is OrchestrationRunStatus.RUNNING
    assert child_run.status is OrchestrationRunStatus.RUNNING


def test_a_child_is_spared_while_a_sibling_has_a_live_agent(session, workspace):
    """Liveness is the whole tree, not the branch — a parent mid-dispatch has
    an open child run with nothing under it yet."""
    parent = _ticket(session, workspace, "F-1")
    busy = _ticket(session, workspace, "T-busy", parent=parent)
    idle = _ticket(session, workspace, "T-idle", parent=parent)
    idle_run = _orch(session, idle, "orch_idle", age=STALE)
    _agent_run(session, busy, "run_live", status=RunStatus.RUNNING, age=STALE)

    assert settle_stalled_orchestrations(session) == []
    session.refresh(idle_run)
    assert idle_run.status is OrchestrationRunStatus.RUNNING


def test_recent_activity_spares_a_tree_with_nothing_in_flight(session, workspace):
    """Between two stages there is no live run at all; that is not a stall."""
    ticket = _ticket(session, workspace, "T-1")
    run = _orch(session, ticket, "orch_1", age=STALE)
    _agent_run(session, ticket, "run_done", status=RunStatus.SUCCEEDED, age=timedelta(minutes=1))

    assert settle_stalled_orchestrations(session) == []
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.RUNNING


# ---- what it must catch ------------------------------------------------


def test_a_stalled_orchestration_is_failed_and_its_lane_released(session, workspace):
    """Ticket 22: nineteen minutes at RUNNING with no agent run, ever."""
    ticket = _ticket(session, workspace, "T-1")
    run = _orch(session, ticket, "orch_1", age=STALE)
    slot = AgentSlot(
        slot_number=1,
        is_available=False,
        current_orchestration_run_id=run.id,
        assigned_at=datetime.now(timezone.utc) - STALE,
    )
    session.add(slot)
    session.commit()

    settled = settle_stalled_orchestrations(session)

    assert [r.id for r in settled] == [run.id]
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.FAILED
    session.refresh(slot)
    assert slot.is_available is True
    assert slot.current_orchestration_run_id is None


def test_a_whole_dead_tree_is_settled(session, workspace):
    """170's tree: parent and both children open, nothing live anywhere."""
    parent = _ticket(session, workspace, "M-1")
    a = _ticket(session, workspace, "F-a", parent=parent)
    b = _ticket(session, workspace, "F-b", parent=parent)
    runs = [
        _orch(session, parent, "orch_p", age=STALE),
        _orch(session, a, "orch_a", age=STALE),
        _orch(session, b, "orch_b", age=STALE),
    ]
    _agent_run(session, b, "run_old", status=RunStatus.SUCCEEDED, age=STALE)

    settled = settle_stalled_orchestrations(session)

    assert {r.id for r in settled} == {r.id for r in runs}


def test_a_stale_claim_that_never_started_is_settled(session, workspace):
    """QUEUED counts: a claim whose thread died before adopting it blocks every
    later start of that ticket on an orchestration that never began."""
    ticket = _ticket(session, workspace, "T-1")
    run = _orch(session, ticket, "orch_1", age=STALE, status=OrchestrationRunStatus.QUEUED)

    assert [r.id for r in settle_stalled_orchestrations(session)] == [run.id]
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.FAILED


def test_a_young_orchestration_is_left_alone(session, workspace):
    ticket = _ticket(session, workspace, "T-1")
    run = _orch(session, ticket, "orch_1", age=timedelta(minutes=1))

    assert settle_stalled_orchestrations(session) == []
    session.refresh(run)
    assert run.status is OrchestrationRunStatus.RUNNING


def test_a_parent_cycle_does_not_hang(session, workspace):
    """The schema permits what the hierarchy rules forbid."""
    a = _ticket(session, workspace, "A")
    b = _ticket(session, workspace, "B", parent=a)
    a.parent_ticket_id = b.id
    session.add(a)
    session.commit()
    _orch(session, a, "orch_a", age=STALE)

    settle_stalled_orchestrations(session)  # must return rather than loop


def test_the_status_read_settles_stalls(session, workspace):
    """There is no periodic tick in this server; the board read is the seam."""
    import asyncio

    from loregarden.services.queue_status import build_queue_status

    ticket = _ticket(session, workspace, "T-1")
    run = _orch(session, ticket, "orch_1", age=STALE)
    slot = AgentSlot(
        slot_number=1,
        is_available=False,
        current_orchestration_run_id=run.id,
        assigned_at=datetime.now(timezone.utc) - STALE,
    )
    session.add(slot)
    session.commit()

    asyncio.run(build_queue_status(session))

    session.refresh(run)
    assert run.status is OrchestrationRunStatus.FAILED
    freed = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert freed.is_available is True


def test_a_fresh_claim_is_not_settled_before_a_driver_adopts_it(session, workspace):
    """`claim_orchestration_run` writes the row before anything runs.

    It leaves `started_at` null until a driver adopts the claim, so judging age
    on that column alone dates every new claim to the beginning of time. This
    settled them on the very next status read, which is every lane dispatch and
    every admission on the machine.
    """
    ticket = _ticket(session, workspace, "T-1")
    claim = OrchestrationRun(
        run_code="orch_claim",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.QUEUED,
    )
    session.add(claim)
    session.commit()
    assert claim.started_at is None

    assert settle_stalled_orchestrations(session) == []
    session.refresh(claim)
    assert claim.status is OrchestrationRunStatus.QUEUED
