from fastapi.testclient import TestClient


def _capability_id(client: TestClient) -> str:
    for t in client.get("/api/tickets?workspace=loregarden").json():
        if t["work_item_type"] == "capability" and t["external_id"].endswith("agent-runtime"):
            return t["id"]
    for t in client.get("/api/tickets?workspace=loregarden").json():
        if t["work_item_type"] == "capability":
            return t["id"]
    raise AssertionError("capability not found")


def test_create_feature_work_item_gets_workflow(client: TestClient):
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Feature-level workflow test",
            "work_item_type": "feature",
            "parent_ticket_id": milestone_id,
            "description": "Runs the standard SDLC workflow at feature scope.",
            "acceptance_criteria": ["Planning through review stages are available"],
            "priority": 2,
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["work_item_type"] == "feature"
    assert body["workflow_stage_key"] == "planning"
    assert len(body["stages"]) >= 5


def test_feature_ticket_backfills_workflow_on_load(client: TestClient):
    feature = next(
        t
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["external_id"] == "m01-backend-platform"
    )
    detail = client.get(f"/api/tickets/{feature['id']}").json()
    assert len(detail["stages"]) >= 5
    assert detail["workflow_stage_key"]


def test_create_task_work_item(client: TestClient):
    parent_id = _capability_id(client)
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Add export filters",
            "work_item_type": "task",
            "parent_ticket_id": parent_id,
            "description": "Filter tickets in the tree view.",
            "acceptance_criteria": ["User can filter by type", "Filters persist in URL"],
            "priority": 2,
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "Add export filters"
    assert body["work_item_type"] == "task"
    assert body["state"] in {"backlog", "in_progress"}
    assert body["workflow_stage_key"] == "planning"
    assert len(body["stages"]) >= 5
    assert body["acceptance_criteria"] == [
        "User can filter by type",
        "Filters persist in URL",
    ]

    listed = client.get("/api/tickets?workspace=loregarden").json()
    assert any(t["id"] == body["id"] for t in listed)


def test_create_task_requires_parent(client: TestClient):
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Orphan task",
            "work_item_type": "task",
        },
    )
    assert res.status_code == 400
    assert "parent" in res.json()["detail"].lower()


def test_create_invalid_hierarchy(client: TestClient):
    task = next(
        t
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "task"
    )
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Bad nesting",
            "work_item_type": "feature",
            "parent_ticket_id": task["id"],
        },
    )
    assert res.status_code == 400
