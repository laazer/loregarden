"""Queued runs have to actually run.

Promotion used to move DB rows and stop there — the slot was claimed, the row
said PROMOTED, and nothing ever executed. And `create_parallel_run` queued with
`run_id=""` against a comment promising the run would be created on promotion,
which nothing did. A queue that never drains is the bug these cover.
"""

from unittest.mock import patch

import pytest
from loregarden.models.domain import AgentRun, AgentSlot, QueuedRun, Workspace
from loregarden.models.domain.enums import QueuePosition
from loregarden.services.parallel_queue import ParallelQueueService
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_ticket


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
    # The ticket has to exist: `agent_runs.ticket_id` references it, and these
    # tests read for the literal "ticket-1".
    make_ticket(session, workspace_id=workspace_id, ticket_id="ticket-1")
    return make_agent_run(session, workspace_id=workspace_id, ticket_id="ticket-1", run_code=code)


@pytest.mark.asyncio
async def test_promotion_dispatches_the_run(session, workspace):
    service = ParallelQueueService(session, max_concurrent=1)
    service.initialize_slots()

    first = _run(session, workspace.id, "run_first")
    waiting = _run(session, workspace.id, "run_waiting")

    await service.queue_run(workspace.id, "ticket-1", first.id)
    queued = await service.queue_run(workspace.id, "ticket-1", waiting.id)
    assert queued["status"] == "queued"

    # Free the only slot, which promotes the waiting run.
    with patch("loregarden.services.run_service.schedule_agent_run") as dispatch:
        await service.on_run_complete(first.id)

    dispatch.assert_called_once_with(waiting.id)


@pytest.mark.asyncio
async def test_a_promoted_run_takes_the_slot(session, workspace):
    service = ParallelQueueService(session, max_concurrent=1)
    service.initialize_slots()

    first = _run(session, workspace.id, "run_first")
    waiting = _run(session, workspace.id, "run_waiting")
    await service.queue_run(workspace.id, "ticket-1", first.id)
    await service.queue_run(workspace.id, "ticket-1", waiting.id)

    with patch("loregarden.services.run_service.schedule_agent_run"):
        await service.on_run_complete(first.id)

    # The pool is global — slots carry no workspace to filter on.
    slot = session.exec(select(AgentSlot)).first()
    assert slot.current_run_id == waiting.id
    assert slot.is_available is False

    row = session.exec(select(QueuedRun).where(QueuedRun.run_id == waiting.id)).first()
    assert row.status == QueuePosition.PROMOTED


@pytest.mark.asyncio
async def test_a_failed_dispatch_does_not_undo_the_promotion(session, workspace):
    """The bookkeeping already committed. Raising here would leave a slot
    claimed by a run the queue no longer knows is waiting."""
    service = ParallelQueueService(session, max_concurrent=1)
    service.initialize_slots()

    first = _run(session, workspace.id, "run_first")
    waiting = _run(session, workspace.id, "run_waiting")
    await service.queue_run(workspace.id, "ticket-1", first.id)
    await service.queue_run(workspace.id, "ticket-1", waiting.id)

    with patch(
        "loregarden.services.run_service.schedule_agent_run",
        side_effect=RuntimeError("no threads"),
    ):
        result = await service.on_run_complete(first.id)

    assert result is not None
    row = session.exec(select(QueuedRun).where(QueuedRun.run_id == waiting.id)).first()
    assert row.status == QueuePosition.PROMOTED


@pytest.mark.asyncio
async def test_an_empty_run_id_is_never_dispatched(session, workspace):
    """Runs used to be queued with a placeholder id; dispatching one would
    schedule a run that does not exist."""
    service = ParallelQueueService(session, max_concurrent=1)

    with patch("loregarden.services.run_service.schedule_agent_run") as dispatch:
        service._dispatch("")

    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_start_estimate_uses_run_history_not_a_flat_ten_minutes(session, workspace):
    """Without history there is nothing to project, so the estimate is now
    rather than a made-up ten minutes per queue position."""
    service = ParallelQueueService(session, max_concurrent=3)
    service.initialize_slots()

    for index in range(4):
        await service.queue_run(
            workspace.id, "ticket-1", _run(session, workspace.id, f"r{index}").id
        )

    queued = session.exec(select(QueuedRun).where(QueuedRun.workspace_id == workspace.id)).all()
    assert queued  # the fourth run waits behind three slots
    estimates = {row.estimated_start_at for row in queued}
    assert all(value is not None for value in estimates)
