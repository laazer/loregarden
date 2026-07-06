from fastapi.testclient import TestClient


def test_workflow_templates_loaded(client: TestClient):
    res = client.get("/api/workflows/templates")
    assert res.status_code == 200
    slugs = {t["slug"] for t in res.json()}
    assert "loregarden-tdd" in slugs
    assert "extended-tdd" in slugs
    assert "blobert-tdd" in slugs


def test_runtime_options(client: TestClient):
    res = client.get("/api/workspaces/runtime-options")
    assert res.status_code == 200
    data = res.json()
    adapter_ids = {opt["id"] for opt in data["cli_adapters"]}
    assert "default" in adapter_ids
    assert "claude" in adapter_ids
    assert len(data["claude_models"]) >= 2


def test_workspace_runtime_settings(client: TestClient):
    get = client.get("/api/workspaces/loregarden/runtime")
    assert get.status_code == 200
    assert get.json()["cli_adapter"] == "default"

    patch = client.patch(
        "/api/workspaces/loregarden/runtime",
        json={"cli_adapter": "claude", "claude_model": "opus", "cursor_model": ""},
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["cli_adapter"] == "claude"
    assert body["claude_model"] == "opus"

    listed = client.get("/api/workspaces").json()
    lore = next(w for w in listed if w["slug"] == "loregarden")
    assert lore["cli_adapter"] == "claude"
    assert lore["claude_model"] == "opus"

    bad = client.patch(
        "/api/workspaces/loregarden/runtime",
        json={"cli_adapter": "not-a-cli", "claude_model": "", "cursor_model": ""},
    )
    assert bad.status_code == 400

    client.patch(
        "/api/workspaces/loregarden/runtime",
        json={"cli_adapter": "default", "claude_model": "", "cursor_model": ""},
    )


def test_workspace_template_switch_changes_stages(client: TestClient):
    loregarden = client.get("/api/workspaces/loregarden/workflow").json()
    assert loregarden["template_slug"] == "loregarden-tdd"
    lore_stages = len(loregarden["stages"])

    patch = client.patch(
        "/api/workspaces/loregarden/workflow",
        json={"workflow_template_slug": "extended-tdd"},
    )
    assert patch.status_code == 200

    extended = client.get("/api/workspaces/loregarden/workflow").json()
    assert extended["template_slug"] == "extended-tdd"
    assert (
        len(extended["stages"]) != lore_stages
        or extended["stages"][1]["key"] == "domain_consultation"
    )

    ticket_id = None
    for t in client.get("/api/tickets").json():
        if t["external_id"] == "01-bootstrap-fastapi-control-plane":
            ticket_id = t["id"]
            break
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert any(s["key"] == "domain_consultation" for s in detail["stages"])

    client.patch(
        "/api/workspaces/loregarden/workflow",
        json={"workflow_template_slug": "loregarden-tdd"},
    )


def test_create_workspace_with_template(client: TestClient):
    res = client.post(
        "/api/workspaces",
        json={
            "slug": "sample-ref",
            "name": "sample-ref",
            "workflow_template_slug": "extended-tdd",
        },
    )
    assert res.status_code == 201
    assert res.json()["workflow_template_slug"] == "extended-tdd"


def test_export_project_board(client: TestClient):
    res = client.post("/api/export/project-board")
    assert res.status_code == 200
    body = res.json()
    assert body["exported"] >= 5
    assert any("01_milestone_bootstrap" in p for p in body["paths"])
