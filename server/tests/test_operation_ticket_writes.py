"""Filing and editing a ticket run through one operation on every transport.

The page's routes had drifted from the agent's tools: they found a ticket (and
a new ticket's parent) by UUID alone, and a PATCH naming a stage status but no
stage answered 200 having set nothing. These pin the reconciled behaviour on
the REST side, where it was missing.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _milestone(client: TestClient) -> dict:
    return next(
        t
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )


def _bug(client: TestClient, parent: str) -> dict:
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Operation write target",
            "work_item_type": "bug",
            "parent_ticket_id": parent,
            "acceptance_criteria": ["One"],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_post_takes_a_parent_by_its_external_id(client: TestClient):
    milestone = _milestone(client)

    created = _bug(client, milestone["external_id"])

    assert created["parent_ticket_id"] == milestone["id"]


def test_patch_finds_the_ticket_by_its_external_id(client: TestClient):
    ticket = _bug(client, _milestone(client)["id"])

    res = client.patch(
        f"/api/tickets/{ticket['external_id']}",
        json={"acceptance_criteria": ["Renamed by external id"]},
    )

    assert res.status_code == 200, res.text
    assert res.json()["id"] == ticket["id"]
    reloaded = client.get(f"/api/tickets/{ticket['id']}").json()
    assert reloaded["acceptance_criteria"] == ["Renamed by external id"]


def test_patch_refuses_a_stage_status_that_names_no_stage(client: TestClient):
    ticket = _bug(client, _milestone(client)["id"])
    before = client.get(f"/api/tickets/{ticket['id']}").json()

    res = client.patch(f"/api/tickets/{ticket['id']}", json={"stage_status": "done"})

    assert res.status_code == 400, res.text
    after = client.get(f"/api/tickets/{ticket['id']}").json()
    assert after["revision"] == before["revision"]
    assert after["workflow_stage_status"] == before["workflow_stage_status"]


def test_patch_with_a_stage_and_no_status_moves_the_cursor(client: TestClient):
    """`update_ticket_manual` dropped a lone `stage_key` and answered 200; the
    agent's `set_ticket_workflow` has always read it as moving the cursor."""
    ticket = _bug(client, _milestone(client)["id"])
    before = client.get(f"/api/tickets/{ticket['id']}").json()
    target = next(
        stage["key"] for stage in before["stages"] if stage["key"] != before["workflow_stage_key"]
    )

    res = client.patch(f"/api/tickets/{ticket['id']}", json={"stage_key": target})

    assert res.status_code == 200, res.text
    assert res.json()["workflow_stage_key"] == target


def test_an_empty_patch_still_answers_with_the_ticket(client: TestClient):
    """The agent's tool refuses an edit that changes nothing; the page's PATCH of
    nothing has always answered with the ticket, and still does."""
    ticket = _bug(client, _milestone(client)["id"])

    res = client.patch(f"/api/tickets/{ticket['id']}", json={})

    assert res.status_code == 200, res.text
    assert res.json()["id"] == ticket["id"]
