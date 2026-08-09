"""REST + MCP surface for symmetric, non-blocking ticket relations."""

from __future__ import annotations

import json

from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
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


def test_relation_is_visible_from_both_ends(client, db_session):
    a = _feature(db_session, "Rel A")
    b = _feature(db_session, "Rel B")

    resp = client.post(f"/api/tickets/{a.id}/relations", json={"related_to": b.external_id})
    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()["related"]] == [b.id]

    # Symmetric: B lists A without a second write.
    assert [r["id"] for r in client.get(f"/api/tickets/{b.id}").json()["related"]] == [a.id]


def test_relation_removal_works_from_either_end(client, db_session):
    a = _feature(db_session, "Rel drop A")
    b = _feature(db_session, "Rel drop B")
    client.post(f"/api/tickets/{a.id}/relations", json={"related_to": b.id})

    # Deleted from the end that did not create it — the pair is stored once, in
    # canonical id order, so this must find it regardless of which id is low.
    removed = client.delete(f"/api/tickets/{b.id}/relations/{a.id}")
    assert removed.status_code == 200
    assert removed.json()["related"] == []
    assert client.get(f"/api/tickets/{a.id}").json()["related"] == []


def test_relating_is_idempotent_in_both_directions(client, db_session):
    a = _feature(db_session, "Rel dup A")
    b = _feature(db_session, "Rel dup B")

    client.post(f"/api/tickets/{a.id}/relations", json={"related_to": b.id})
    resp = client.post(f"/api/tickets/{b.id}/relations", json={"related_to": a.id})

    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()["related"]] == [a.id]


def test_api_rejects_self_relation(client, db_session):
    a = _feature(db_session, "Rel self")
    resp = client.post(f"/api/tickets/{a.id}/relations", json={"related_to": a.id})
    assert resp.status_code == 400


def test_api_404s_on_unknown_related_ticket(client, db_session):
    a = _feature(db_session, "Rel missing")
    resp = client.post(f"/api/tickets/{a.id}/relations", json={"related_to": "no-such-ticket"})
    assert resp.status_code == 404


def test_relation_does_not_create_a_dependency(client, db_session):
    """The whole point of the feature: related tickets do not wait for each other."""
    a = _feature(db_session, "Rel not dep A")
    b = _feature(db_session, "Rel not dep B")

    detail = client.post(f"/api/tickets/{a.id}/relations", json={"related_to": b.id}).json()

    assert detail["dependencies"] == []
    assert detail["dependents"] == []


# --- MCP -------------------------------------------------------------------


def _call(db_session, tool: str, args: dict) -> dict:
    return json.loads(execute_tool(db_session, tool, normalize_tool_arguments(tool, args)))


def test_mcp_link_and_unlink_relation(db_session):
    a = _feature(db_session, "MCP rel A")
    b = _feature(db_session, "MCP rel B")

    linked = _call(
        db_session,
        "loregarden_link_relation",
        {"ticket_id": a.external_id, "related_to": b.external_id},
    )
    assert [r["id"] for r in linked["related"]] == [b.id]

    unlinked = _call(
        db_session,
        "loregarden_unlink_relation",
        {"ticket_id": b.external_id, "related_to": a.external_id},
    )
    assert unlinked["related"] == []
