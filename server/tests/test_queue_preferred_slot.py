"""A ticket staged into a slot should start in that slot.

The queue board draws slots as lanes you drop a ticket into, so the slot the
card sits in has to be the slot the run claims. Before this, `queue_run` took
whatever `.first()` returned and a ticket staged in slot 3 could start in slot
1 — the card jumped the moment you pressed Start.
"""

from unittest.mock import patch

import pytest
from loregarden.models.domain import AgentRun, AgentSlot, Workspace
from loregarden.services.parallel_queue import ParallelQueueService
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


def _run(session: Session, workspace_id: str, code: str) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id="ticket-1",
        workspace_id=workspace_id,
        agent_id="backend_implementer",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _slot_for(session: Session, run_id: str) -> AgentSlot:
    """The slot holding this run. The pool is global, so nothing scopes it."""
    return session.exec(select(AgentSlot).where(AgentSlot.current_run_id == run_id)).one()


@pytest.mark.asyncio
async def test_preferred_slot_is_honoured(session, workspace):
    service = ParallelQueueService(session, max_concurrent=3)
    service.initialize_slots()

    run = _run(session, workspace.id, "run_staged")
    result = await service.queue_run(workspace.id, "ticket-1", run.id, preferred_slot=3)

    assert result["status"] == "started"
    assert result["slot_number"] == 3
    assert _slot_for(session, run.id).slot_number == 3


@pytest.mark.asyncio
async def test_no_preference_takes_the_lowest_free_slot(session, workspace):
    service = ParallelQueueService(session, max_concurrent=3)
    service.initialize_slots()

    run = _run(session, workspace.id, "run_any")
    result = await service.queue_run(workspace.id, "ticket-1", run.id)

    assert result["slot_number"] == 1


@pytest.mark.asyncio
async def test_taken_preferred_slot_falls_back_rather_than_queueing(session, workspace):
    """Between staging and Start, a promotion can take the slot you picked.

    Starting the run is what was asked for; the slot number is presentation, so
    it takes another free slot instead of waiting behind the queue.
    """
    service = ParallelQueueService(session, max_concurrent=3)
    service.initialize_slots()

    occupant = _run(session, workspace.id, "run_occupant")
    await service.queue_run(workspace.id, "ticket-1", occupant.id, preferred_slot=2)

    latecomer = _run(session, workspace.id, "run_latecomer")
    result = await service.queue_run(workspace.id, "ticket-1", latecomer.id, preferred_slot=2)

    assert result["status"] == "started"
    assert result["slot_number"] == 1
    assert _slot_for(session, latecomer.id).slot_number == 1


@pytest.mark.asyncio
async def test_preferred_slot_still_queues_when_nothing_is_free(session, workspace):
    service = ParallelQueueService(session, max_concurrent=1)
    service.initialize_slots()

    occupant = _run(session, workspace.id, "run_occupant")
    await service.queue_run(workspace.id, "ticket-1", occupant.id)

    waiting = _run(session, workspace.id, "run_waiting")
    result = await service.queue_run(workspace.id, "ticket-1", waiting.id, preferred_slot=1)

    assert result["status"] == "queued"
    assert result["position"] == 1


@pytest.mark.asyncio
async def test_the_slot_pool_is_shared_across_workspaces(session, workspace):
    """The point of migration 0058.

    Slots used to be keyed by workspace, so two workspaces meant two pools of
    three and six concurrent agents on a box sized for three. Filling the pool
    from one workspace must now leave nothing for the other.
    """
    other = Workspace(slug="other", name="other", repo_path=".")
    session.add(other)
    session.commit()
    session.refresh(other)

    service = ParallelQueueService(session, max_concurrent=2)
    service.initialize_slots()

    for i in range(2):
        run = _run(session, workspace.id, f"run_fill_{i}")
        started = await service.queue_run(workspace.id, "ticket-1", run.id)
        assert started["status"] == "started"

    # The other workspace finds the machine busy rather than its own free pool.
    latecomer = _run(session, other.id, "run_other")
    result = await service.queue_run(other.id, "ticket-1", latecomer.id)

    assert result["status"] == "queued"
    assert len(session.exec(select(AgentSlot)).all()) == 2


@pytest.mark.asyncio
async def test_one_waiting_line_orders_across_workspaces(session, workspace):
    """A freed slot goes to whoever waited longest, not to the workspace that
    freed it."""
    other = Workspace(slug="other", name="other", repo_path=".")
    session.add(other)
    session.commit()
    session.refresh(other)

    service = ParallelQueueService(session, max_concurrent=1)
    service.initialize_slots()

    occupant = _run(session, workspace.id, "run_occupant")
    await service.queue_run(workspace.id, "ticket-1", occupant.id)

    # The other workspace queues first, so it is ahead in the shared line.
    first_waiter = _run(session, other.id, "run_other_waiting")
    queued_other = await service.queue_run(other.id, "ticket-1", first_waiter.id)
    second_waiter = _run(session, workspace.id, "run_same_waiting")
    queued_same = await service.queue_run(workspace.id, "ticket-1", second_waiter.id)

    assert (queued_other["position"], queued_same["position"]) == (1, 2)

    with patch("loregarden.services.run_service.schedule_agent_run"):
        await service.on_run_complete(occupant.id)

    assert _slot_for(session, first_waiter.id).slot_number == 1
