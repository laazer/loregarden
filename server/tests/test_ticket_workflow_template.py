"""Per-ticket workflow template assignment."""

from fastapi.testclient import TestClient


def _task_ticket(client: TestClient) -> dict:
    capability = next(
        t
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "capability"
    )
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Per-ticket workflow test",
            "work_item_type": "task",
            "parent_ticket_id": capability["id"],
            "description": "Ticket for workflow template assignment test",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_assign_workflow_template_to_ticket(client: TestClient):
    ticket = _task_ticket(client)
    assert ticket["workflow_template_slug"] == "loregarden-tdd"
    lore_stages = {s["key"] for s in ticket["stages"]}
    assert "domain_consultation" not in lore_stages

    patch = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"workflow_template_slug": "extended-tdd"},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["workflow_template_slug"] == "extended-tdd"
    assert body["workflow_template_name"] == "Extended TDD"
    assert any(s["key"] == "domain_consultation" for s in body["stages"])
    assert body["workflow_stage_key"] == "planning"


def test_ticket_template_preserved_when_workspace_default_changes(client: TestClient):
    ticket = _task_ticket(client)
    patch = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"workflow_template_slug": "extended-tdd"},
    )
    assert patch.status_code == 200

    client.patch(
        "/api/workspaces/loregarden/workflow",
        json={"workflow_template_slug": "loregarden-tdd"},
    )

    detail = client.get(f"/api/tickets/{ticket['id']}").json()
    assert detail["workflow_template_slug"] == "extended-tdd"
    assert any(s["key"] == "domain_consultation" for s in detail["stages"])


def test_clear_workflow_template_from_ticket(client: TestClient):
    ticket = _task_ticket(client)
    assert ticket["workflow_template_slug"] == "loregarden-tdd"
    assert len(ticket["stages"]) > 0

    patch = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"workflow_template_slug": ""},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["workflow_template_slug"] == ""
    assert body["workflow_template_name"] == ""
    assert body["stages"] == []
    assert body["workflow_stage_key"] == ""

    detail = client.get(f"/api/tickets/{ticket['id']}").json()
    assert detail["workflow_template_slug"] == ""
    assert detail["stages"] == []


def test_reassign_workflow_after_clearing(client: TestClient):
    ticket = _task_ticket(client)
    clear = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"workflow_template_slug": ""},
    )
    assert clear.status_code == 200

    patch = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"workflow_template_slug": "extended-tdd"},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["workflow_template_slug"] == "extended-tdd"
    assert any(s["key"] == "domain_consultation" for s in body["stages"])
    assert body["workflow_stage_key"] == "planning"
