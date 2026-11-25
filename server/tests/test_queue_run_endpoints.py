"""The queue endpoints the client and the Claude Code plugin call, over HTTP."""

from unittest import mock

import pytest
from loregarden.models.domain import AgentSlot, QueuedRun, QueuePosition, RunStatus
from loregarden.services.parallel_queue import ParallelQueueService
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_workspace, queued_run

#: Spelled out, not imported: the test decides which, not the code under test.
CANCELLABLE = (QueuePosition.QUEUED, QueuePosition.SCHEDULED, QueuePosition.PAUSED)
NOT_CANCELLABLE = [status for status in QueuePosition if status not in CANCELLABLE]


def _entry(session: Session, run_id: str, position: int, status=QueuePosition.QUEUED) -> QueuedRun:
    entry = queued_run(
        session,
        run_id=run_id,
        ticket_id=f"ticket-{run_id}",
        workspace_id="ws-queue",
        status=status,
        position=position,
    )
    session.add(entry)
    session.commit()
    return entry


def _waiting(session: Session) -> list[tuple[str | None, int]]:
    session.expire_all()
    rows = session.exec(select(QueuedRun).order_by(QueuedRun.position)).all()
    return [(row.run_id, row.position) for row in rows]


@pytest.mark.parametrize("status", CANCELLABLE)
def test_cancelling_removes_the_entry_and_closes_the_gap(client, db_session, status):
    _entry(db_session, "first", 1, status)
    _entry(db_session, "second", 2)

    res = client.post("/api/parallel/queue/first/cancel")

    assert (res.status_code, res.json()["status"]) == (200, "cancelled")
    assert _waiting(db_session) == [("second", 1)]


@pytest.mark.parametrize("status", NOT_CANCELLABLE)
def test_an_entry_that_is_not_waiting_is_refused_and_kept(client, db_session, status):
    """Deleting it stopped nothing and lost the record, while answering "cancelled"."""
    _entry(db_session, "ran", 1, status)

    assert client.post("/api/parallel/queue/ran/cancel").status_code == 404
    assert _waiting(db_session) == [("ran", 1)]


def test_cancelling_an_unknown_run_is_404(client):
    assert client.post("/api/parallel/queue/nope/cancel").status_code == 404


def test_a_cancel_that_failed_is_not_reported_as_not_found(client, db_session):
    _entry(db_session, "first", 1)

    with mock.patch.object(
        ParallelQueueService, "_reorder_queue_sync", side_effect=RuntimeError("database is locked")
    ):
        res = client.post("/api/parallel/queue/first/cancel")

    assert (res.status_code, res.json()["detail"]) == (500, "database is locked")


def test_the_run_lists_are_empty_when_nothing_is_queued_or_running(client):
    assert client.get("/api/parallel/active-runs").json() == {"active_runs": []}
    assert client.get("/api/parallel/queued-runs").json() == {"queued_runs": []}


def test_the_run_lists_hold_the_running_and_the_waiting_run(client, db_session):
    make_workspace(db_session, workspace_id="ws-queue", slug="ws-queue")
    run = make_agent_run(
        db_session,
        workspace_id="ws-queue",
        ticket_id="ticket-live",
        run_id="live",
        status=RunStatus.RUNNING,
    )
    slot = db_session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).first()
    slot = slot or AgentSlot(slot_number=1)
    slot.is_available = False
    slot.current_run_id = run.id
    db_session.add(slot)
    db_session.commit()
    _entry(db_session, "waiting", 1)

    active = client.get("/api/parallel/active-runs").json()["active_runs"]
    queued = client.get("/api/parallel/queued-runs").json()["queued_runs"]

    assert [(card["run_id"], card["slot_number"]) for card in active] == [("live", 1)]
    assert [(card["run_id"], card["position"]) for card in queued] == [("waiting", 1)]
