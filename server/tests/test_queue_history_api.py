"""The Clear buttons' endpoints: history cutoff and per-lane dismissal."""

from unittest.mock import patch

from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    Ticket,
    Workspace,
)
from sqlmodel import Session, select


def _failed_entry(session: Session, slot_number: int) -> QueuedRun:
    workspace = session.exec(select(Workspace)).first()
    ticket = Ticket(external_id=f"t-api-{slot_number}", workspace_id=workspace.id, title="T")
    session.add(ticket)
    session.commit()
    orchestration = OrchestrationRun(
        run_code=f"orch_api_{slot_number}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.FAILED,
    )
    session.add(orchestration)
    session.commit()
    entry = QueuedRun(
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        orchestration_run_id=orchestration.id,
        slot_number=slot_number,
        status=QueuePosition.STARTED,
    )
    session.add(entry)
    session.commit()
    return entry


def test_clear_history_endpoint_empties_the_rail(client, isolated_db):
    with Session(isolated_db) as session:
        _failed_entry(session, 1)

    before = client.get("/api/parallel/lanes/history").json()
    assert before["total"] >= 1
    assert before["cleared_at"] is None

    response = client.post("/api/parallel/lanes/history/clear")
    assert response.status_code == 200
    cleared_at = response.json()["cleared_at"]

    after = client.get("/api/parallel/lanes/history").json()
    assert after["total"] == 0
    assert after["cleared_at"] is not None
    assert after["cleared_at"][:19] == cleared_at[:19]


def test_dismiss_lane_attention_endpoint_reports_the_count(client, isolated_db):
    with Session(isolated_db) as session:
        _failed_entry(session, 2)

    with patch("loregarden.api.queue_lanes.emit_execution_update") as emit:
        first = client.post("/api/parallel/lanes/2/attention/dismiss")
        second = client.post("/api/parallel/lanes/2/attention/dismiss")

    assert first.json()["dismissed"] == 1
    assert second.json()["dismissed"] == 0
    assert emit.call_count == 1
