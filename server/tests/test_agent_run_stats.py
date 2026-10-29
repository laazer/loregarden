from datetime import datetime

from fastapi.testclient import TestClient
from loregarden.models.domain import RunStatus
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace


def _runs(
    session: Session, agent_id: str, *statuses: RunStatus, at: datetime | None = None
) -> None:
    workspace = make_workspace(session, slug="run-stats")
    for index, status in enumerate(statuses):
        fields = {"created_at": at} if at else {}
        make_agent_run(
            session,
            workspace_id=workspace.id,
            agent_id=agent_id,
            run_code=f"{agent_id}-{index}",
            status=status,
            **fields,
        )


def _by_agent(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/studio/agent-run-stats")
    assert response.status_code == 200
    return {row["agent_id"]: row for row in response.json()}


def test_counts_finished_runs_and_failures_per_agent(
    client: TestClient, db_session: Session
) -> None:
    _runs(db_session, "repair", RunStatus.FAILED, RunStatus.FAILED, RunStatus.SUCCEEDED)
    _runs(db_session, "planner", RunStatus.SUCCEEDED, RunStatus.CANCELLED)

    stats = _by_agent(client)

    assert (stats["repair"]["runs"], stats["repair"]["failed"]) == (3, 2)
    # A cancelled run finished without failing.
    assert (stats["planner"]["runs"], stats["planner"]["failed"]) == (2, 0)


def test_a_run_still_going_is_not_counted_as_a_success(
    client: TestClient, db_session: Session
) -> None:
    _runs(db_session, "static_qa", RunStatus.FAILED, RunStatus.RUNNING, RunStatus.QUEUED)

    row = _by_agent(client)["static_qa"]

    assert (row["runs"], row["failed"]) == (1, 1)


def test_an_agent_with_only_unfinished_runs_is_listed_with_its_last_run(
    client: TestClient, db_session: Session
) -> None:
    started = datetime(2026, 10, 28, 12, 0, 0)
    _runs(db_session, "triage", RunStatus.RUNNING, at=started)

    row = _by_agent(client)["triage"]

    assert (row["runs"], row["failed"]) == (0, 0)
    assert row["last_run_at"].startswith("2026-10-28T12:00:00")


def test_leaves_out_runs_with_no_agent_id(client: TestClient, db_session: Session) -> None:
    _runs(db_session, "", RunStatus.FAILED)

    assert "" not in _by_agent(client)


def test_is_not_shadowed_by_the_agent_detail_route(client: TestClient) -> None:
    # `/agents/{slug}` would answer 404 "Agent not found" for a sibling path.
    assert client.get("/api/studio/agent-run-stats").json() == []
