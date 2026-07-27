"""REST + MCP surface for ticket dependency management."""

from __future__ import annotations

import json

from loregarden.mcp.tools import execute_tool, normalize_tool_arguments, tool_names
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.ticket_service import TicketService
from sqlmodel import select


def _ws(session) -> Workspace:
    return session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()


def _milestone(session) -> Ticket:
    ws = _ws(session)
    return session.exec(
        select(Ticket).where(
            Ticket.workspace_id == ws.id, Ticket.work_item_type == WorkItemType.MILESTONE
        )
    ).first()


def _feature(session, title: str) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug="loregarden",
        title=title,
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=_milestone(session).id,
    )


# --- REST ------------------------------------------------------------------


def test_add_and_remove_dependency_via_api(client, db_session):
    a = _feature(db_session, "Dep A")
    b = _feature(db_session, "Dep B")

    resp = client.post(f"/api/tickets/{a.id}/dependencies", json={"depends_on": b.external_id})
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert [d["id"] for d in detail["dependencies"]] == [b.id]

    # The prerequisite sees A as a dependent.
    b_detail = client.get(f"/api/tickets/{b.id}").json()
    assert [d["id"] for d in b_detail["dependents"]] == [a.id]

    removed = client.delete(f"/api/tickets/{a.id}/dependencies/{b.id}")
    assert removed.status_code == 200
    assert removed.json()["dependencies"] == []


def test_api_rejects_dependency_cycle_with_409(client, db_session):
    a = _feature(db_session, "Cycle A")
    b = _feature(db_session, "Cycle B")
    assert (
        client.post(f"/api/tickets/{a.id}/dependencies", json={"depends_on": b.id}).status_code
        == 200
    )
    conflict = client.post(f"/api/tickets/{b.id}/dependencies", json={"depends_on": a.id})
    assert conflict.status_code == 409


def test_api_missing_prerequisite_is_an_error(client, db_session):
    a = _feature(db_session, "Solo")
    resp = client.post(f"/api/tickets/{a.id}/dependencies", json={"depends_on": "no-such-ticket"})
    assert resp.status_code in (400, 404)


# --- MCP -------------------------------------------------------------------


def _call(session, name: str, args: dict) -> dict:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def test_dependency_tools_are_registered():
    assert "loregarden_link_dependency" in tool_names()
    assert "loregarden_unlink_dependency" in tool_names()


def test_mcp_link_and_unlink_dependency(client, db_session):
    a = _feature(db_session, "MCP Dep A")
    b = _feature(db_session, "MCP Dep B")

    linked = _call(
        db_session,
        "loregarden_link_dependency",
        {"ticket_id": a.external_id, "depends_on": b.external_id},
    )
    assert [d["id"] for d in linked["depends_on"]] == [b.id]

    unlinked = _call(
        db_session,
        "loregarden_unlink_dependency",
        {"ticket_id": a.external_id, "depends_on": b.external_id},
    )
    assert unlinked["depends_on"] == []


def test_mcp_link_dependency_rejects_cycle(client, db_session):
    import pytest

    a = _feature(db_session, "MCP Cycle A")
    b = _feature(db_session, "MCP Cycle B")
    _call(db_session, "loregarden_link_dependency", {"ticket_id": a.id, "depends_on": b.id})
    with pytest.raises(ValueError):
        _call(db_session, "loregarden_link_dependency", {"ticket_id": b.id, "depends_on": a.id})
