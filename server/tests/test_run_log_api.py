"""GET /api/runs/{run_id}/log — the source for the run-log modal."""

import json

from fastapi.testclient import TestClient
from loregarden.models.domain import AgentRun, Artifact, RunStatus, Ticket
from sqlmodel import Session, select


def _seed_run(session: Session, *, status: RunStatus = RunStatus.SUCCEEDED) -> AgentRun:
    ticket = session.exec(select(Ticket)).first()
    assert ticket
    run = AgentRun(
        run_code="run_modal1",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        skill_name="run_tests",
        stage_key="testing",
        status=status,
        command="claude -p 'run the tests'",
        stdout='{"type":"result","result":"raw stream-json that must not be served"}',
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _seed_log(session: Session, run: AgentRun, lines: list[dict], live: str | None = None) -> None:
    session.add(
        Artifact(
            ticket_id=run.ticket_id,
            run_id=run.id,
            kind="log",
            title="Run log",
            content_json=json.dumps({"lines": lines, "live": live}),
        )
    )
    session.commit()


def test_run_log_returns_rendered_lines(client: TestClient, db_session: Session):
    run = _seed_run(db_session)
    _seed_log(
        db_session,
        run,
        [
            {"time": "20:57:14", "tag": "RUN", "text": "static_qa invoked"},
            {"time": "20:57:20", "tag": "OUT", "text": "3 passed"},
        ],
        live="still working",
    )

    res = client.get(f"/api/runs/{run.id}/log")
    assert res.status_code == 200
    body = res.json()

    assert [line["text"] for line in body["lines"]] == ["static_qa invoked", "3 passed"]
    assert body["live"] == "still working"
    assert body["run_code"] == "run_modal1"
    assert body["agent_id"] == "static_qa"
    assert body["stage_key"] == "testing"
    assert body["command"] == "claude -p 'run the tests'"


def test_run_log_does_not_serve_raw_stdout(client: TestClient, db_session: Session):
    """stdout is the unbounded raw transcript — the modal must never receive it."""
    run = _seed_run(db_session)
    _seed_log(db_session, run, [{"time": "20:57:14", "tag": "RUN", "text": "hello"}])

    body = client.get(f"/api/runs/{run.id}/log").json()

    assert "stdout" not in body
    assert "raw stream-json" not in json.dumps(body)


def test_run_log_without_artifact_returns_empty_lines(client: TestClient, db_session: Session):
    """Runs predating the log streamer still resolve, so the modal shows identity."""
    run = _seed_run(db_session)

    res = client.get(f"/api/runs/{run.id}/log")
    assert res.status_code == 200
    body = res.json()
    assert body["lines"] == []
    assert body["live"] is None
    assert body["run_code"] == "run_modal1"


def test_run_log_missing_run_returns_404(client: TestClient):
    assert client.get("/api/runs/does-not-exist/log").status_code == 404
