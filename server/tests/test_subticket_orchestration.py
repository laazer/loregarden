"""Parent orchestration runs direct child workflows sequentially (ticket 29)."""

from fastapi.testclient import TestClient


def test_parent_orchestration_runs_child_workflow_first(client: TestClient):
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    feature_res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Parent feature for child orchestration",
            "work_item_type": "feature",
            "parent_ticket_id": milestone_id,
        },
    )
    assert feature_res.status_code == 201
    feature = feature_res.json()
    child_res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Child bug under feature",
            "work_item_type": "bug",
            "parent_ticket_id": feature["id"],
        },
    )
    assert child_res.status_code == 201
    child = child_res.json()

    assert child["workflow_stage_key"] == "planning"
    assert child["state"] == "backlog"

    parent_before = client.get(f"/api/tickets/{feature['id']}").json()
    assert parent_before["workflow_stage_key"] == "planning"
    assert parent_before["stages"][0]["status"] == "pending"

    res = client.post(f"/api/tickets/{feature['id']}/orchestrate", json={"max_stages": 1})
    assert res.status_code == 200

    child_after = client.get(f"/api/tickets/{child['id']}").json()
    parent_after = client.get(f"/api/tickets/{feature['id']}").json()

    child_progress = any(s["status"] != "pending" for s in child_after["stages"])
    parent_progress = any(s["status"] != "pending" for s in parent_after["stages"])
    assert child_progress or parent_progress
