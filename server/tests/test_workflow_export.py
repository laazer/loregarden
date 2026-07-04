import pytest
from fastapi.testclient import TestClient


def test_workflow_templates_loaded(client: TestClient):
    res = client.get("/api/workflows/templates")
    assert res.status_code == 200
    slugs = {t["slug"] for t in res.json()}
    assert "loregarden-tdd" in slugs
    assert "extended-tdd" in slugs


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
    assert len(extended["stages"]) != lore_stages or extended["stages"][1]["key"] == "domain_consultation"

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
