from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from loregarden.models.domain import BlockKind, RunStatus
from sqlmodel import Session
from tests.factories import make_agent_run, make_ticket, make_workspace

START = datetime(2026, 10, 28, 12, 0, 0)


def _record(client: TestClient, agent_id: str) -> dict:
    response = client.get(f"/api/studio/agent-runs/{agent_id}")
    assert response.status_code == 200
    return response.json()


def test_failures_per_stage_split_by_who_could_unblock_them(
    client: TestClient, db_session: Session
) -> None:
    workspace = make_workspace(db_session, slug="run-record")
    runs = [
        ("integration-merge", RunStatus.FAILED, BlockKind.HARNESS),
        ("integration-merge", RunStatus.FAILED, BlockKind.HARNESS),
        ("integration-merge", RunStatus.FAILED, BlockKind.WORK),
        ("integration-merge", RunStatus.SUCCEEDED, None),
        ("triage", RunStatus.FAILED, None),
        ("review", RunStatus.FAILED, BlockKind.CONFLICT),
        ("triage", RunStatus.CANCELLED, None),
        ("implement", RunStatus.SUCCEEDED, None),
        # Unfinished: not yet a success or a failure.
        ("implement", RunStatus.RUNNING, None),
    ]
    for index, (stage, status, kind) in enumerate(runs):
        make_agent_run(
            db_session,
            workspace_id=workspace.id,
            agent_id="repair",
            run_code=f"repair-{index}",
            stage_key=stage,
            status=status,
            failure_kind=kind,
        )
    make_agent_run(
        db_session,
        workspace_id=workspace.id,
        agent_id="planner",
        run_code="planner-0",
        stage_key="triage",
        status=RunStatus.FAILED,
    )

    stages = {row["stage_key"]: row for row in _record(client, "repair")["stages"]}

    assert [*stages] == ["integration-merge", "triage", "review", "implement"]
    merge = stages["integration-merge"]
    assert (merge["runs"], merge["failed"]) == (4, 3)
    assert merge["failed_by_kind"] == [
        {"kind": "harness", "count": 2},
        {"kind": "work", "count": 1},
    ]
    triage = stages["triage"]
    assert (triage["runs"], triage["failed"]) == (2, 1)
    assert triage["failed_by_kind"] == [{"kind": None, "count": 1}]
    # Every kind counts, not only the three a repair run can act on.
    assert stages["review"]["failed_by_kind"] == [{"kind": "conflict", "count": 1}]
    assert (stages["implement"]["runs"], stages["implement"]["failed"]) == (1, 0)


def test_recent_runs_are_newest_first_with_their_ticket(
    client: TestClient, db_session: Session
) -> None:
    workspace = make_workspace(db_session, slug="run-record-recent")
    ticket = make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="lg-light-waiter-1925",
        title="A light waiter",
    )
    for index in range(14):
        make_agent_run(
            db_session,
            workspace_id=workspace.id,
            ticket_id=ticket.id if index == 13 else None,
            agent_id="repair",
            run_code=f"repair-{index}",
            status=RunStatus.FAILED,
            created_at=START + timedelta(minutes=index),
        )

    recent = _record(client, "repair")["recent"]

    assert len(recent) == 12
    assert [run["run_code"] for run in recent[:2]] == ["repair-13", "repair-12"]
    assert (recent[0]["ticket_external_id"], recent[0]["ticket_title"]) == (
        "lg-light-waiter-1925",
        "A light waiter",
    )
    assert recent[1]["ticket_external_id"] is None


def test_an_agent_that_never_ran_has_an_empty_record(client: TestClient) -> None:
    assert _record(client, "never_ran") == {"agent_id": "never_ran", "stages": [], "recent": []}
