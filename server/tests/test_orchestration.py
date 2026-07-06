from fastapi.testclient import TestClient
from loregarden.models.domain import OrchestrationRunStatus


def test_orchestration_profile_loaded(client: TestClient):
    res = client.get("/api/orchestration/workspaces/loregarden/profile")
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "loregarden"
    assert body["driver"] == "builtin_autopilot"


def test_external_mcp_start_orchestration(client: TestClient):
    ticket_id = None
    for t in client.get("/api/tickets").json():
        if t["external_id"] == "02-bootstrap-react-ide-shell":
            ticket_id = t["id"]
            break
    res = client.post(
        f"/api/orchestration/tickets/{ticket_id}/start",
        json={"driver": "external_mcp"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["driver"] == "external_mcp"
    assert body["status"] == OrchestrationRunStatus.RUNNING.value


def test_orchestrate_ticket_one_stage(client: TestClient):
    ticket_id = None
    for t in client.get("/api/tickets").json():
        if t["external_id"] == "04-workflow-template-overrides":
            ticket_id = t["id"]
            break
    res = client.post(
        f"/api/tickets/{ticket_id}/orchestrate",
        json={"max_stages": 1},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["state"] in ("in_progress", "blocked", "done")


def test_orchestration_callbacks(client: TestClient):
    ticket_id = None
    for t in client.get("/api/tickets").json():
        if t["external_id"] == "03-wire-cli-agent-runner":
            ticket_id = t["id"]
            break
    started = client.post(
        f"/api/orchestration/tickets/{ticket_id}/start",
        json={"driver": "external_mcp"},
    ).json()
    run_id = started["id"]

    state = client.get(f"/api/orchestration/tickets/{ticket_id}/state").json()
    assert state["ticket_id"] == ticket_id

    complete = client.post(
        f"/api/orchestration/runs/{run_id}/complete_stage",
        json={"stage_key": "planning", "next_agent": "spec"},
    )
    assert complete.status_code == 200
    assert complete.json()["ok"] is True

    done = client.post(
        f"/api/orchestration/runs/{run_id}/complete",
        json={"status": "succeeded"},
    )
    assert done.status_code == 200


def test_get_ticket_by_external_id(client: TestClient):
    res = client.get(
        "/api/orchestration/tickets/by-external/loregarden/01-bootstrap-fastapi-control-plane/state"
    )
    assert res.status_code == 200
    assert res.json()["external_id"] == "01-bootstrap-fastapi-control-plane"
