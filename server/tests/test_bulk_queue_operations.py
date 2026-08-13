"""Bulk queue endpoints write the status they claim to write.

`bulk-cancel` reported every run "cancelled" and set it to QUEUED — which for
an entry a lane was already running re-armed it for dispatch rather than
cancelling anything. `QueuePosition.CANCELLED` was never written anywhere in
the codebase, so `queue_history.derive_outcome`'s cancelled branch could not be
reached at all.
"""

import pytest
from loregarden.models.domain import QueuedRun, QueuePosition, Ticket, Workspace
from sqlmodel import Session, select


@pytest.fixture(name="workspace")
def workspace_fixture(db_session):
    ws = db_session.exec(select(Workspace)).first()
    assert ws is not None
    return ws


def _entry(session: Session, workspace, *, run_id: str, status: QueuePosition) -> QueuedRun:
    ticket = Ticket(external_id=f"T-{run_id}", workspace_id=workspace.id, title=run_id)
    session.add(ticket)
    session.commit()
    entry = QueuedRun(
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        run_id=run_id,
        slot_number=1,
        status=status,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _cancel(client, workspace, run_ids: list[str]) -> dict:
    res = client.post(
        f"/api/parallel/workspace/{workspace.id}/queue/bulk-cancel",
        json=run_ids,
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_cancelling_a_waiting_entry_marks_it_cancelled(client, db_session, workspace):
    entry = _entry(db_session, workspace, run_id="run-waiting", status=QueuePosition.QUEUED)

    body = _cancel(client, workspace, ["run-waiting"])

    assert body["successful"] == 1
    db_session.refresh(entry)
    assert entry.status == QueuePosition.CANCELLED


def test_cancelling_a_running_entry_is_refused(client, db_session, workspace):
    """Flipping the row does not stop an agent.

    Setting QUEUED — what this used to do — put a live entry back in its lane's
    waiting list, so `start_lane_head` would dispatch the ticket a second time.
    """
    entry = _entry(db_session, workspace, run_id="run-live", status=QueuePosition.ACTIVE)

    body = _cancel(client, workspace, ["run-live"])

    assert body["successful"] == 0
    assert body["failed"] == 1
    assert "cannot cancel" in body["results"][0]["message"]
    db_session.refresh(entry)
    assert entry.status == QueuePosition.ACTIVE


def test_cancelling_reports_a_missing_run_without_touching_others(client, db_session, workspace):
    entry = _entry(db_session, workspace, run_id="run-real", status=QueuePosition.SCHEDULED)

    body = _cancel(client, workspace, ["run-ghost", "run-real"])

    assert body["successful"] == 1
    assert body["failed"] == 1
    db_session.refresh(entry)
    assert entry.status == QueuePosition.CANCELLED


def test_failed_run_reads_select_the_failed_entries(client, db_session, workspace):
    """The failed-run reads are typed against the enum, not the bare string."""
    _entry(db_session, workspace, run_id="run-failed", status=QueuePosition.FAILED)
    _entry(db_session, workspace, run_id="run-ok", status=QueuePosition.QUEUED)

    res = client.get(f"/api/parallel/workspace/{workspace.id}/queue/failed-runs")

    assert res.status_code == 200
    assert [row["run_id"] for row in res.json()] == ["run-failed"]
