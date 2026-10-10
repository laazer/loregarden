"""Managing initiatives end to end: create, list across workspaces, attach,
detach, and the parent rollup that follows a changed child set."""

from __future__ import annotations

import json
from typing import Any

import pytest
from loregarden.mcp.tools import TOOL_DEFINITIONS, execute_tool, normalize_tool_arguments
from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select


def _call(session: Session, name: str, args: dict[str, Any]) -> Any:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


@pytest.fixture(name="second_workspace")
def second_workspace_fixture(db_session: Session) -> Workspace:
    seeded = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ws = Workspace(
        slug="elsewhere",
        name="Elsewhere",
        repo_path="/tmp/elsewhere",
        workflow_template_id=seeded.workflow_template_id,
    )
    db_session.add(ws)
    db_session.commit()
    return ws


def _initiative(session: Session, title: str = "Ship the thing") -> Ticket:
    return TicketService(session).create_ticket(title=title, work_item_type=WorkItemType.INITIATIVE)


def _milestone(session: Session, slug: str, title: str, parent: str | None = None) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug=slug,
        title=title,
        work_item_type=WorkItemType.MILESTONE,
        parent_ticket_id=parent,
    )


def _set_state(session: Session, ticket: Ticket, state: TicketState) -> None:
    ticket.state = state
    session.add(ticket)
    session.commit()


def test_rest_create_initiative_without_workspace_slug(client):
    res = client.post("/api/tickets", json={"title": "Q4", "work_item_type": "initiative"})
    assert res.status_code in (200, 201), res.text
    body = res.json()
    assert body["work_item_type"] == "initiative"
    assert body["workspace_slug"] == ""
    assert body["external_id"].startswith("init-")


def test_rest_create_milestone_still_requires_workspace(client):
    res = client.post("/api/tickets", json={"title": "M", "work_item_type": "milestone"})
    assert res.status_code == 400


def test_list_initiatives_spans_workspaces(client, db_session: Session, second_workspace):
    initiative = _initiative(db_session)
    here = _milestone(db_session, "loregarden", "Here", initiative.id)
    there = _milestone(db_session, second_workspace.slug, "There", initiative.id)
    _set_state(db_session, there, TicketState.DONE)

    res = client.get("/api/initiatives")
    assert res.status_code == 200
    [row] = [r for r in res.json() if r["id"] == initiative.id]
    assert {m["id"]: m["workspace_slug"] for m in row["milestones"]} == {
        here.id: "loregarden",
        there.id: "elsewhere",
    }
    assert row["progress"] == {"resolved": 1, "total": 2}
    assert row["workspaces"] == ["elsewhere", "loregarden"]

    detail = client.get(f"/api/initiatives/{initiative.id}")
    assert detail.status_code == 200
    assert detail.json()["progress"] == {"resolved": 1, "total": 2}


def test_get_initiative_404_for_non_initiative(client, db_session: Session):
    milestone = _milestone(db_session, "loregarden", "Not an initiative")
    assert client.get(f"/api/initiatives/{milestone.id}").status_code == 404


def test_attachable_milestones_lists_only_unowned(client, db_session: Session, second_workspace):
    initiative = _initiative(db_session)
    owned = _milestone(db_session, "loregarden", "Owned", initiative.id)
    free = _milestone(db_session, second_workspace.slug, "Free")

    res = client.get("/api/initiatives/attachable-milestones")
    assert res.status_code == 200
    ids = {m["id"] for m in res.json()}
    assert free.id in ids
    assert owned.id not in ids


def test_attach_and_detach_reconciles_both_parents(client, db_session: Session):
    initiative = _initiative(db_session)
    milestone = _milestone(db_session, "loregarden", "Finished already")
    _set_state(db_session, milestone, TicketState.DONE)

    res = client.patch(f"/api/tickets/{milestone.id}", json={"parent_ticket_id": initiative.id})
    assert res.status_code == 200, res.text
    db_session.refresh(initiative)
    # The initiative gained a finished child: it is done without waiting for a sweep.
    assert initiative.state == TicketState.DONE

    open_one = _milestone(db_session, "loregarden", "Still open")
    _set_state(db_session, open_one, TicketState.IN_PROGRESS)
    client.patch(f"/api/tickets/{open_one.id}", json={"parent_ticket_id": initiative.id})
    db_session.refresh(initiative)
    assert initiative.state == TicketState.IN_PROGRESS

    res = client.patch(f"/api/tickets/{open_one.id}", json={"parent_ticket_id": ""})
    assert res.status_code == 200, res.text
    db_session.refresh(initiative)
    db_session.refresh(open_one)
    assert open_one.parent_ticket_id is None
    assert initiative.state == TicketState.DONE


def test_delete_open_child_reconciles_parent(client, db_session: Session):
    initiative = _initiative(db_session)
    done = _milestone(db_session, "loregarden", "Done", initiative.id)
    _set_state(db_session, done, TicketState.DONE)
    doomed = _milestone(db_session, "loregarden", "Doomed", initiative.id)
    _set_state(db_session, doomed, TicketState.IN_PROGRESS)
    client.patch(f"/api/tickets/{doomed.id}", json={"parent_ticket_id": initiative.id})

    assert client.delete(f"/api/tickets/{doomed.id}").status_code in (200, 204)
    db_session.refresh(initiative)
    assert initiative.state == TicketState.DONE


def test_mcp_create_initiative_without_workspace(db_session: Session):
    payload = _call(
        db_session,
        "loregarden_create_ticket",
        {"title": "MCP initiative", "work_item_type": "initiative"},
    )
    ticket = db_session.get(Ticket, payload["id"])
    assert ticket is not None
    assert ticket.work_item_type == WorkItemType.INITIATIVE
    assert ticket.workspace_id is None


def test_mcp_list_initiatives_is_global(db_session: Session):
    initiative = _initiative(db_session, "Global list")
    payload = _call(db_session, "loregarden_list_tickets", {"work_item_type": "initiative"})
    assert initiative.id in {row["id"] for row in payload["tickets"]}
    assert all(row["work_item_type"] == "initiative" for row in payload["tickets"])


def test_mcp_list_without_workspace_or_initiative_filter_fails_loudly(db_session: Session):
    with pytest.raises(ValueError, match="workspace_slug is required"):
        _call(db_session, "loregarden_list_tickets", {})


def test_mcp_schemas_advertise_initiative():
    by_name = {tool["name"]: tool["inputSchema"] for tool in TOOL_DEFINITIONS}
    for name in ("loregarden_create_ticket", "loregarden_list_tickets"):
        schema = by_name[name]
        assert "initiative" in schema["properties"]["work_item_type"]["enum"]
        assert "workspace_slug" not in schema.get("required", [])
