"""Editing acceptance criteria after a ticket exists.

PATCH used to accept acceptance_criteria, discard it, and answer 200. The write
never landed and nothing said so, which is worse than a rejection: an agent that
hit it stashed its criteria in the description instead. These tests pin both
halves — that the field is now applied, and that an unknown field fails loudly
rather than repeating the silent drop with some other name.
"""

from fastapi.testclient import TestClient
from tests.mcp_helpers import call_mcp


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

    body = call_mcp(
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

    call_mcp(client, "loregarden_update_ticket", args)
    call_mcp(client, "loregarden_update_ticket", args)

    assert client.get(f"/api/tickets/{ticket['id']}").json()["acceptance_criteria"] == [
        "Existing one",
        "Existing two",
        "Added three",
    ]


def test_mcp_update_ticket_still_sets_state(client: TestClient):
    ticket = _make_ticket(client, ["Unchanged"])

    call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "state": "in_progress"},
    )

    reloaded = client.get(f"/api/tickets/{ticket['id']}").json()
    assert reloaded["state"] == "in_progress"
    assert reloaded["acceptance_criteria"] == ["Unchanged"]


def test_mcp_update_ticket_rejects_a_call_that_changes_nothing(client: TestClient):
    ticket = _make_ticket(client, ["Untouched"])

    body = call_mcp(client, "loregarden_update_ticket", {"ticket_id": ticket["id"]})

    assert body.get("error") or body["result"].get("isError"), body


def test_mcp_update_ticket_rejects_mode_without_criteria(client: TestClient):
    ticket = _make_ticket(client, ["Untouched"])

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "state": "done", "mode": "append"},
    )

    assert body.get("error") or body["result"].get("isError"), body


def test_mcp_update_ticket_can_release_a_pinned_state(client: TestClient):
    """Naming a state pins it, and MCP had no way to unpin it again.

    `state_locked` is how an operator says "I decided this", and three readers
    honour it: the workflow's own derivation, the parent rollup, and queue
    repair. It was write-only from MCP — reachable, never releasable — so an
    agent that set a state took the ticket out of all three permanently.
    """
    ticket = _make_ticket(client, ["Unchanged"])

    call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "state": "in_progress"},
    )
    assert client.get(f"/api/tickets/{ticket['id']}").json()["state_locked"] is True

    call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "auto_state": True},
    )

    assert client.get(f"/api/tickets/{ticket['id']}").json()["state_locked"] is False


def test_mcp_update_ticket_accepts_auto_state_on_its_own(client: TestClient):
    """`auto_state` alone is a change, so it must not trip the "nothing to update" guard."""
    ticket = _make_ticket(client, ["Untouched"])

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "auto_state": True},
    )

    assert not body.get("error") and not body["result"].get("isError"), body


def test_a_new_ticket_has_nothing_checked(client: TestClient):
    ticket = _make_ticket(client, ["One", "Two"])

    assert ticket["checked_acceptance_criteria"] == []


def test_patch_checks_criteria_without_bumping_revision(client: TestClient):
    ticket = _make_ticket(client, ["One", "Two", "Three"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"checked_acceptance_criteria": ["Three", "One"]},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    # Returned in criteria order, whatever order the caller sent.
    assert body["checked_acceptance_criteria"] == ["One", "Three"]
    assert body["revision"] == ticket["revision"]
    reloaded = client.get(f"/api/tickets/{ticket['id']}").json()
    assert reloaded["checked_acceptance_criteria"] == ["One", "Three"]


def test_patch_drops_checks_for_criteria_the_ticket_lacks(client: TestClient):
    ticket = _make_ticket(client, ["One", "Two"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"checked_acceptance_criteria": ["One", "Not a criterion"]},
    )

    assert res.status_code == 200, res.text
    assert res.json()["checked_acceptance_criteria"] == ["One"]


def test_rewording_a_criterion_clears_its_check_and_keeps_the_rest(client: TestClient):
    ticket = _make_ticket(client, ["One", "Two"])
    client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"checked_acceptance_criteria": ["One", "Two"]},
    )

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"acceptance_criteria": ["Two", "One reworded"]},
    )

    assert res.status_code == 200, res.text
    assert res.json()["checked_acceptance_criteria"] == ["Two"]
    # Pruned in storage too, so restoring the old wording does not resurrect it.
    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"acceptance_criteria": ["Two", "One"]},
    )
    assert res.json()["checked_acceptance_criteria"] == ["Two"]


def test_patch_judges_checks_against_criteria_sent_with_them(client: TestClient):
    ticket = _make_ticket(client, ["One"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"acceptance_criteria": ["One", "New"], "checked_acceptance_criteria": ["New"]},
    )

    assert res.status_code == 200, res.text
    assert res.json()["checked_acceptance_criteria"] == ["New"]


def _make_done_ticket(client: TestClient, criteria: list[str], checked: list[str]) -> dict:
    ticket = _make_ticket(client, criteria)
    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"checked_acceptance_criteria": checked, "state": "done"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "done"
    return res.json()


def test_patch_refuses_to_change_a_done_tickets_criteria(client: TestClient):
    """A done ticket's criteria are the record of what was delivered."""
    ticket = _make_done_ticket(client, ["Delivered"], ["Delivered"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"acceptance_criteria": ["Delivered", "Sneaked in later"]},
    )

    assert res.status_code == 400, res.text
    assert "done" in res.json()["detail"]
    assert client.get(f"/api/tickets/{ticket['id']}").json()["acceptance_criteria"] == ["Delivered"]


def test_patch_refuses_to_change_a_done_tickets_checks(client: TestClient):
    ticket = _make_done_ticket(client, ["One", "Two"], ["One"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"checked_acceptance_criteria": ["One", "Two"]},
    )

    assert res.status_code == 400, res.text
    assert client.get(f"/api/tickets/{ticket['id']}").json()["checked_acceptance_criteria"] == [
        "One"
    ]


def test_patch_of_a_done_ticket_resending_unchanged_criteria_succeeds(client: TestClient):
    """The edit form sends every field back on save; an unchanged list must pass."""
    ticket = _make_done_ticket(client, ["One", "Two"], ["Two"])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={
            "title": "Retitled after delivery",
            "acceptance_criteria": ["One", "Two"],
            "checked_acceptance_criteria": ["Two"],
        },
    )

    assert res.status_code == 200, res.text
    assert res.json()["title"] == "Retitled after delivery"


def test_reopening_a_done_ticket_unfreezes_its_criteria(client: TestClient):
    ticket = _make_done_ticket(client, ["Old"], [])

    res = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"state": "backlog", "acceptance_criteria": ["Old", "New"]},
    )

    assert res.status_code == 200, res.text
    assert res.json()["acceptance_criteria"] == ["Old", "New"]


def test_mcp_update_ticket_refuses_to_append_to_a_done_ticket(client: TestClient):
    ticket = _make_done_ticket(client, ["Delivered"], [])

    body = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket["id"], "acceptance_criteria": ["Later"], "mode": "append"},
    )

    assert body.get("error") or body["result"].get("isError"), body
    assert client.get(f"/api/tickets/{ticket['id']}").json()["acceptance_criteria"] == ["Delivered"]


def test_a_done_ticket_can_still_be_related_to_follow_up_work(client: TestClient):
    """Relations are how follow-up work attaches to a delivered ticket."""
    done = _make_done_ticket(client, ["Delivered"], [])
    follow_up = _make_ticket(client, ["Follow-up"])

    body = call_mcp(
        client,
        "loregarden_link_relation",
        {"ticket_id": done["id"], "related_to": follow_up["id"]},
    )

    assert "error" not in body and not body["result"].get("isError"), body
