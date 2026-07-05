"""Backlog ticket feature tests."""

from fastapi.testclient import TestClient


def test_ticket_branch_patch(client: TestClient):
    ticket = next(t for t in client.get("/api/tickets?workspace=loregarden").json() if t["work_item_type"] == "task")
    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"branch": "feature/test-branch"},
    )
    assert res.status_code == 200
    assert res.json()["branch"] == "feature/test-branch"


def test_orchestrate_accepts_auto_approve_and_stop_at(client: TestClient):
    ticket = next(t for t in client.get("/api/tickets?workspace=loregarden").json() if t["work_item_type"] == "task")
    res = client.post(
        f"/api/tickets/{ticket['id']}/orchestrate",
        json={"auto_approve": True, "stop_at_stage_key": "planning"},
    )
    assert res.status_code == 200, res.text
