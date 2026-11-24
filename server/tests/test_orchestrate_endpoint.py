"""POST /api/tickets/{id}/orchestrate: which stages one request runs, and what
it refuses. The seeded ticket has two agent stages left (testing, review) before
a human gate, so every limit has an exact answer."""

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import AgentRun
from sqlmodel import Session, select

OPEN_TICKET = "04-workflow-template-overrides"
DONE_TICKET = "05-self-tracking-milestone"


def _ticket_id(client: TestClient, legacy_id: str) -> str:
    return next(
        t["id"] for t in client.get("/api/tickets").json() if t["legacy_external_id"] == legacy_id
    )


def _stages_run(session: Session, ticket_id: str) -> list[str]:
    session.expire_all()
    runs = session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket_id).order_by(AgentRun.created_at)
    ).all()
    return [run.stage_key for run in runs]


def _stage_status(body: dict, key: str) -> str:
    return next(stage["status"] for stage in body["stages"] if stage["key"] == key)


def test_a_limit_runs_that_many_stages_and_the_next_call_resumes_after_them(
    client: TestClient, db_session: Session
):
    ticket_id = _ticket_id(client, OPEN_TICKET)
    url = f"/api/tickets/{ticket_id}/orchestrate"

    first = client.post(url, json={"max_stages": 1})
    assert first.status_code == 200
    assert _stages_run(db_session, ticket_id) == ["testing"]
    assert _stage_status(first.json(), "testing") == "done"
    assert _stage_status(first.json(), "review") == "pending"

    second = client.post(url, json={"max_stages": 1})
    assert second.status_code == 200
    assert _stages_run(db_session, ticket_id) == ["testing", "review"]
    assert _stage_status(second.json(), "review") == "done"


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {},
        {"json": {}},
        {"json": {"max_stages": None}},
        {"json": {"max_stages": 0}},
        {"json": {"max_stages": 999999}},
    ],
    ids=["no-body", "empty-body", "null", "zero-is-unlimited", "above-the-stage-count"],
)
def test_without_an_effective_limit_it_runs_every_stage_up_to_the_human_gate(
    client: TestClient, db_session: Session, request_kwargs: dict
):
    ticket_id = _ticket_id(client, OPEN_TICKET)

    res = client.post(f"/api/tickets/{ticket_id}/orchestrate", **request_kwargs)

    assert res.status_code == 200
    assert res.json()["id"] == ticket_id
    assert _stages_run(db_session, ticket_id) == ["testing", "review"]
    assert _stage_status(res.json(), "approval") == "awaiting"


def test_a_done_ticket_runs_nothing(client: TestClient, db_session: Session):
    ticket_id = _ticket_id(client, DONE_TICKET)

    res = client.post(f"/api/tickets/{ticket_id}/orchestrate", json={})

    assert res.status_code == 200
    assert res.json()["state"] == "done"
    assert _stages_run(db_session, ticket_id) == []


def test_a_second_start_is_refused_while_one_is_running(client: TestClient, db_session: Session):
    ticket_id = _ticket_id(client, OPEN_TICKET)
    started = client.post(
        f"/api/orchestration/tickets/{ticket_id}/start", json={"driver": "external_mcp"}
    )
    assert started.status_code == 200

    res = client.post(f"/api/tickets/{ticket_id}/orchestrate", json={})

    assert res.status_code == 400
    assert _stages_run(db_session, ticket_id) == []


def test_an_unknown_ticket_is_404(client: TestClient):
    res = client.post("/api/tickets/00000000-0000-0000-0000-000000000000/orchestrate", json={})
    assert res.status_code == 404


@pytest.mark.parametrize("max_stages", ["invalid", 1.5, -1])
def test_a_limit_that_is_not_a_whole_number_of_stages_is_refused(
    client: TestClient, db_session: Session, max_stages: object
):
    """A negative limit used to mean "unlimited": the loop only compares limits
    above zero, so asking for -1 ran every stage."""
    ticket_id = _ticket_id(client, OPEN_TICKET)

    res = client.post(f"/api/tickets/{ticket_id}/orchestrate", json={"max_stages": max_stages})

    assert res.status_code == 422
    assert _stages_run(db_session, ticket_id) == []
