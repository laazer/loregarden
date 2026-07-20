"""Editing acceptance criteria after a ticket exists.

PATCH used to accept acceptance_criteria, discard it, and answer 200. The write
never landed and nothing said so, which is worse than a rejection: an agent that
hit it stashed its criteria in the description instead. These tests pin both
halves — that the field is now applied, and that an unknown field fails loudly
rather than repeating the silent drop with some other name.
"""

from fastapi.testclient import TestClient


def _make_ticket(client: TestClient, criteria: list[str]) -> dict:
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Acceptance criteria edit target",
            "work_item_type": "bug",
            "parent_ticket_id": milestone_id,
            "description": "Fixture for acceptance criteria editing.",
            "acceptance_criteria": criteria,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _call_mcp(client: TestClient, tool: str, args: dict) -> dict:
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_patch_replaces_acceptance_criteria(client: TestClient):
    ticket = _make_ticket(client, ["Original one", "Original two"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"acceptance_criteria": ["Replaced one", "Replaced two", "Replaced three"]},
    )

    assert res.status_code == 200, res.text
    assert res.json()["acceptance_criteria"] == [
        "Replaced one",
        "Replaced two",
        "Replaced three",
    ]
    reloaded = client.get(f"/api/tickets/{ticket['id']}").json()
    assert reloaded["acceptance_criteria"] == [
        "Replaced one",
        "Replaced two",
        "Replaced three",
    ]


def test_patch_normalizes_criteria_like_create_does(client: TestClient):
    ticket = _make_ticket(client, ["Original"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"acceptance_criteria": ["  padded  ", "", "   ", "kept"]},
    )

    assert res.json()["acceptance_criteria"] == ["padded", "kept"]


def test_patch_without_criteria_leaves_them_alone(client: TestClient):
    ticket = _make_ticket(client, ["Keep me"])

    res = client.patch(f"/api/tickets/{ticket['id']}", json={"title": "Retitled only"})

    assert res.status_code == 200, res.text
    assert res.json()["title"] == "Retitled only"
    assert res.json()["acceptance_criteria"] == ["Keep me"]


def test_patch_with_empty_list_clears_criteria(client: TestClient):
    ticket = _make_ticket(client, ["Doomed"])

    res = client.patch(f"/api/tickets/{ticket['id']}", json={"acceptance_criteria": []})

    assert res.status_code == 200, res.text
    assert res.json()["acceptance_criteria"] == []


def test_patch_rejects_unknown_field_instead_of_ignoring_it(client: TestClient):
    """The actual defect: an unmodelled field used to vanish behind a 200."""
    ticket = _make_ticket(client, ["Untouched"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"acceptanceCriteria": ["camelCase is not the field name"]},
    )

    assert res.status_code == 422, res.text
    assert client.get(f"/api/tickets/{ticket['id']}").json()["acceptance_criteria"] == ["Untouched"]


def test_editing_criteria_records_a_human_revision(client: TestClient):
    ticket = _make_ticket(client, ["Before"])
    before = client.get(f"/api/tickets/{ticket['id']}").json()

    client.patch(f"/api/tickets/{ticket['id']}", json={"acceptance_criteria": ["After"]})

    after = client.get(f"/api/tickets/{ticket['id']}").json()
    assert after["revision"] == before["revision"] + 1
    assert after["last_updated_by"] == "human"


def test_rewriting_identical_criteria_does_not_bump_revision(client: TestClient):
    ticket = _make_ticket(client, ["Same"])
    before = client.get(f"/api/tickets/{ticket['id']}").json()

    client.patch(f"/api/tickets/{ticket['id']}", json={"acceptance_criteria": ["Same"]})

    assert client.get(f"/api/tickets/{ticket['id']}").json()["revision"] == before["revision"]


def test_mcp_update_ticket_replaces_criteria(client: TestClient):
    ticket = _make_ticket(client, ["Old one", "Old two"])

    body = _call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "acceptance_criteria": ["Fresh"]},
    )

    assert "error" not in body, body
    assert client.get(f"/api/tickets/{ticket['id']}").json()["acceptance_criteria"] == ["Fresh"]


def test_mcp_update_ticket_appends_without_duplicating(client: TestClient):
    """Append is the mode that motivated this tool: add criteria, keep the rest.

    Stages here re-run — a gate autofix retry is routine — so an append replayed
    verbatim must not leave the ticket holding each criterion twice.
    """
    ticket = _make_ticket(client, ["Existing one", "Existing two"])
    args = {
        "ticket_id": ticket["id"],
        "acceptance_criteria": ["Existing two", "Added three"],
        "mode": "append",
    }

    _call_mcp(client, "loregarden_update_ticket", args)
    _call_mcp(client, "loregarden_update_ticket", args)

    assert client.get(f"/api/tickets/{ticket['id']}").json()["acceptance_criteria"] == [
        "Existing one",
        "Existing two",
        "Added three",
    ]


def test_mcp_update_ticket_still_sets_state(client: TestClient):
    ticket = _make_ticket(client, ["Unchanged"])

    _call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "state": "in_progress"},
    )

    reloaded = client.get(f"/api/tickets/{ticket['id']}").json()
    assert reloaded["state"] == "in_progress"
    assert reloaded["acceptance_criteria"] == ["Unchanged"]


def test_mcp_update_ticket_rejects_a_call_that_changes_nothing(client: TestClient):
    ticket = _make_ticket(client, ["Untouched"])

    body = _call_mcp(client, "loregarden_update_ticket", {"ticket_id": ticket["id"]})

    assert body.get("error") or body["result"].get("isError"), body


def test_mcp_update_ticket_rejects_mode_without_criteria(client: TestClient):
    ticket = _make_ticket(client, ["Untouched"])

    body = _call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "state": "done", "mode": "append"},
    )

    assert body.get("error") or body["result"].get("isError"), body
