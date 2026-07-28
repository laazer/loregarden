"""Raw ticket artifact feed for the Artifacts tab."""

from __future__ import annotations

import json

from loregarden.models.domain import Artifact, Ticket, TicketState, Workspace
from loregarden.services.artifact_service import list_ticket_artifacts
from sqlmodel import Session, select


def _workspace(session: Session) -> Workspace:
    ws = session.exec(select(Workspace)).first()
    if ws:
        return ws
    ws = Workspace(slug="ws-artifacts", name="Artifacts WS", repo_path="/tmp")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session: Session, workspace_id: str) -> Ticket:
    ticket = Ticket(
        external_id="art-1",
        title="Artifact feed",
        state=TicketState.IN_PROGRESS,
        workspace_id=workspace_id,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def test_list_ticket_artifacts_newest_first(db_session: Session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    older = Artifact(
        ticket_id=ticket.id,
        kind="analysis",
        title="First note",
        content_json=json.dumps({"n": 1}),
    )
    newer = Artifact(
        ticket_id=ticket.id,
        kind="test_spec",
        title="Second note",
        content_json=json.dumps({"n": 2}),
    )
    db_session.add(older)
    db_session.commit()
    db_session.add(newer)
    db_session.commit()

    body = list_ticket_artifacts(db_session, ticket.id)
    assert body["total"] == 2
    assert [item["title"] for item in body["items"]] == ["Second note", "First note"]
    assert body["items"][0]["kind"] == "test_spec"
    assert body["items"][0]["content"] == {"n": 2}
    assert body["items"][0]["content_bytes"] > 0


def test_get_ticket_artifacts_endpoint(client, db_session: Session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind="source_analysis",
            title="Gate runner",
            content_json=json.dumps({"files": ["gate_runner.py"]}),
        )
    )
    db_session.commit()

    resp = client.get(f"/api/tickets/{ticket.id}/artifacts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["kind"] == "source_analysis"
    assert body["items"][0]["content"]["files"] == ["gate_runner.py"]


def test_get_ticket_artifacts_404(client):
    assert client.get("/api/tickets/nope/artifacts").status_code == 404
