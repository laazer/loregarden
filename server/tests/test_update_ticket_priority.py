"""PATCH /tickets/{id} accepts priority (1–3) so it's editable from the details modal."""

from fastapi.testclient import TestClient


def _make_ticket(client: TestClient) -> dict:
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Priority edit target",
            "work_item_type": "bug",
            "parent_ticket_id": milestone_id,
            "priority": 3,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_patch_priority_is_applied(client: TestClient):
    ticket = _make_ticket(client)
    res = client.patch(f"/api/tickets/{ticket['id']}", json={"priority": 1})
    assert res.status_code == 200, res.text
    assert res.json()["priority"] == 1
    # And it persisted.
    assert client.get(f"/api/tickets/{ticket['id']}").json()["priority"] == 1


def test_patch_out_of_range_priority_is_rejected(client: TestClient):
    ticket = _make_ticket(client)
    res = client.patch(f"/api/tickets/{ticket['id']}", json={"priority": 5})
    assert res.status_code == 400, res.text
    # Unchanged.
    assert client.get(f"/api/tickets/{ticket['id']}").json()["priority"] == 3
