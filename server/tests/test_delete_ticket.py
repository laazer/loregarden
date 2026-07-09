from fastapi.testclient import TestClient


def _create(client: TestClient, **overrides) -> dict:
    body = {
        "workspace_slug": "loregarden",
        "title": "Delete me",
        "work_item_type": "feature",
        **overrides,
    }
    res = client.post("/api/tickets", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_delete_ticket_removes_it(client: TestClient):
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    feature = _create(client, parent_ticket_id=milestone_id)

    res = client.delete(f"/api/tickets/{feature['id']}")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    res = client.get(f"/api/tickets/{feature['id']}")
    assert res.status_code == 404


def test_delete_ticket_blocks_when_children_exist(client: TestClient):
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    feature = _create(client, parent_ticket_id=milestone_id)
    _create(
        client,
        title="Child capability",
        work_item_type="capability",
        parent_ticket_id=feature["id"],
    )

    res = client.delete(f"/api/tickets/{feature['id']}")
    assert res.status_code == 400
    assert "child" in res.json()["detail"].lower()

    res = client.get(f"/api/tickets/{feature['id']}")
    assert res.status_code == 200


def test_delete_ticket_missing_returns_404(client: TestClient):
    res = client.delete("/api/tickets/does-not-exist")
    assert res.status_code == 404
