"""Raw ticket artifact feed for the Artifacts tab."""

from __future__ import annotations

import json

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    StageBudgetArtifactKind,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.artifact_records import (
    RUN_CONTEXT_ARTIFACT_TITLE,
    run_log_artifact_title,
)
from loregarden.services.artifact_service import list_ticket_artifacts
from sqlmodel import Session, select
from tests.factories import make_agent_run


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


def _by_title(body: dict) -> dict[str, dict]:
    return {item["title"]: item for item in body["items"]}


def test_feed_names_the_stage_from_the_run_then_from_the_content(db_session: Session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    run = make_agent_run(
        db_session,
        workspace_id=ws.id,
        ticket_id=ticket.id,
        run_code="run_feed01",
        stage_key="implement",
    )
    db_session.add_all(
        [
            # The run wins over whatever the content claims.
            Artifact(
                ticket_id=ticket.id,
                run_id=run.id,
                kind="handoff",
                title="from the run",
                content_json=json.dumps({"stage_key": "plan"}),
            ),
            Artifact(
                ticket_id=ticket.id,
                kind="error",
                title="from the content",
                content_json=json.dumps({"stage_key": "gate"}),
            ),
            Artifact(ticket_id=ticket.id, kind="plan", title="no stage", content_json="{}"),
            Artifact(ticket_id=ticket.id, kind="data", title="a list", content_json="[1, 2]"),
            Artifact(
                ticket_id=ticket.id,
                kind="data",
                title="a non-string stage",
                content_json=json.dumps({"stage_key": 3}),
            ),
        ]
    )
    db_session.commit()

    items = _by_title(list_ticket_artifacts(db_session, ticket.id))
    assert items["from the run"]["stage_key"] == "implement"
    assert items["from the content"]["stage_key"] == "gate"
    assert items["no stage"]["stage_key"] is None
    assert items["a list"]["stage_key"] is None
    assert items["a non-string stage"]["stage_key"] is None


def test_feed_marks_the_platforms_bookkeeping_as_system(db_session: Session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    run = make_agent_run(
        db_session,
        workspace_id=ws.id,
        ticket_id=ticket.id,
        run_code="run_feed02",
        stage_key="gate",
    )
    db_session.add_all(
        [
            Artifact(
                ticket_id=ticket.id,
                kind=StageBudgetArtifactKind.DISPATCH,
                title="stage-dispatch:gate",
            ),
            Artifact(
                ticket_id=ticket.id,
                run_id=run.id,
                kind=ArtifactKind.CONTEXT,
                title=RUN_CONTEXT_ARTIFACT_TITLE,
            ),
            Artifact(
                ticket_id=ticket.id,
                run_id=run.id,
                kind=ArtifactKind.LOG,
                title=run_log_artifact_title(run.run_code),
            ),
            # Work output, even where the shape is close to a system record.
            Artifact(ticket_id=ticket.id, run_id=run.id, kind=ArtifactKind.LOG, title="Run notes"),
            # Names a run, but no run of its own: an agent's title, not a pointer.
            Artifact(ticket_id=ticket.id, kind=ArtifactKind.LOG, title="Run run_elsewhere"),
            Artifact(ticket_id=ticket.id, kind=ArtifactKind.CONTEXT, title="Stage report — gate"),
        ]
    )
    db_session.commit()

    system = {
        item["title"]: item["system"]
        for item in list_ticket_artifacts(db_session, ticket.id)["items"]
    }
    assert system == {
        "stage-dispatch:gate": True,
        RUN_CONTEXT_ARTIFACT_TITLE: True,
        "Run run_feed02": True,
        "Run notes": False,
        "Run run_elsewhere": False,
        "Stage report — gate": False,
    }
