from fastapi.testclient import TestClient


def _ticket_id_by_external_id(client: TestClient, external_id: str) -> str:
    for t in client.get("/api/tickets").json():
        if t["external_id"] == external_id:
            return t["id"]
    raise AssertionError(f"ticket not found: {external_id}")


def _ticket_detail(client: TestClient, ticket_id: str) -> dict:
    res = client.get(f"/api/tickets/{ticket_id}")
    assert res.status_code == 200
    return res.json()


def test_create_milestone_ticket(client: TestClient):
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "New milestone from test",
            "work_item_type": "milestone",
            "description": "Created via API test",
            "priority": 3,
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "New milestone from test"
    assert body["work_item_type"] == "milestone"
    assert body["workspace_slug"] == "loregarden"
    assert body["state"] == "backlog"


def test_health(client: TestClient):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_list_tickets_seeded(client: TestClient):
    res = client.get("/api/tickets")
    assert res.status_code == 200
    tickets = res.json()
    assert len(tickets) >= 5
    ids = {t["external_id"] for t in tickets}
    assert "01-bootstrap-fastapi-control-plane" in ids


def test_ticket_tree_hierarchy(client: TestClient):
    res = client.get("/api/tickets/tree")
    assert res.status_code == 200
    tree = res.json()
    assert tree
    root = tree[0]
    assert root["work_item_type"] == "milestone"
    assert root["children"]
    flat_types = {root["work_item_type"]}
    stack = list(root["children"])
    while stack:
        node = stack.pop()
        flat_types.add(node["work_item_type"])
        stack.extend(node["children"])
    assert "feature" in flat_types
    assert "capability" in flat_types
    assert "task" in flat_types


def test_ticket_detail_has_stages(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "01-bootstrap-fastapi-control-plane")
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert "stages" in detail
    assert len(detail["stages"]) >= 5


def test_start_run_specific_stage(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    started = client.post(
        f"/api/tickets/{ticket_id}/start",
        json={"manual": True, "stage_key": "planning"},
    )
    assert started.status_code == 200
    body = _ticket_detail(client, ticket_id)
    assert body["workflow_stage_key"] == "planning"
    assert body["workflow_stage_status"] == "done"
    stages = {s["key"]: s["status"] for s in body["stages"]}
    assert stages["planning"] == "done"


def test_start_run_bootstraps_live_log(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    started = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert started.status_code == 200
    body = started.json()
    assert body["artifacts"]["logs"]
    assert any(line["tag"] == "RUN" for line in body["artifacts"]["logs"])


def test_start_run_success_updates_stage(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    started = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert started.status_code == 200
    body = started.json()
    assert body["workflow_stage_status"] in {"running", "done"}
    if body["workflow_stage_status"] == "running":
        body = _ticket_detail(client, ticket_id)
    assert body["workflow_stage_status"] == "done"
    assert body["artifacts"]["logs"]


def test_start_run_failure_blocks_ticket(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")
    ticket_id = _ticket_id_by_external_id(client, "01-bootstrap-fastapi-control-plane")
    started = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert started.status_code == 200
    body = _ticket_detail(client, ticket_id)
    assert body["state"] == "blocked"
    assert body["workflow_stage_status"] == "blocked"
    assert "forced to fail" in body["blocking_issues"].lower()


def test_runs_api_after_start(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    runs = client.get(f"/api/runs?ticket_id={ticket_id}").json()
    assert runs
    assert runs[0]["status"] == "succeeded"
    assert "local_runner" in runs[0]["command"]
    detail = client.get(f"/api/runs/{runs[0]['id']}").json()
    assert detail["run_code"] == runs[0]["run_code"]


def test_agents_registry(client: TestClient):
    res = client.get("/api/agents")
    assert res.status_code == 200
    agents = res.json()
    assert any(a["id"] == "planner" for a in agents)


def test_approvals_inbox(client: TestClient):
    res = client.get("/api/inbox/approvals")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
