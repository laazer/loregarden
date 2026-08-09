"""Ticket tags: normalization at the seam, plus the REST and MCP write paths."""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_tags import MAX_TAG_LENGTH, MAX_TAGS, load_tags, normalize_tags
from sqlmodel import select


def _feature(session, title: str) -> Ticket:
    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    milestone = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == ws.id, Ticket.work_item_type == WorkItemType.MILESTONE
        )
    ).first()
    return TicketService(session).create_ticket(
        workspace_slug="loregarden",
        title=title,
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=milestone.id,
    )


# --- Normalization ---------------------------------------------------------


def test_normalize_strips_blanks_and_case_insensitive_duplicates():
    assert normalize_tags([" backend ", "", "  ", "Backend", "ui"]) == ["backend", "ui"]


def test_normalize_rejects_an_overlong_tag():
    with pytest.raises(ValueError, match="longer than"):
        normalize_tags(["x" * (MAX_TAG_LENGTH + 1)])


def test_normalize_rejects_too_many_tags():
    with pytest.raises(ValueError, match="at most"):
        normalize_tags([f"tag-{i}" for i in range(MAX_TAGS + 1)])


def test_load_tolerates_rows_written_before_the_column_existed():
    assert load_tags(None) == []
    assert load_tags("") == []
    assert load_tags("not json") == []
    assert load_tags('{"a": 1}') == []


# --- REST ------------------------------------------------------------------


def test_patch_sets_and_clears_tags(client, db_session):
    ticket = _feature(db_session, "Tagged")

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"tags": ["backend", " backend ", "api"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tags"] == ["backend", "api"]

    cleared = client.patch(f"/api/tickets/{ticket.id}", json={"tags": []})
    assert cleared.json()["tags"] == []


def test_omitting_tags_leaves_them_alone(client, db_session):
    ticket = _feature(db_session, "Tag keep")
    client.patch(f"/api/tickets/{ticket.id}", json={"tags": ["keep-me"]})

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"title": "Tag keep renamed"})

    assert resp.json()["tags"] == ["keep-me"]


def test_tags_appear_on_the_ticket_list(client, db_session):
    ticket = _feature(db_session, "Tag listed")
    client.patch(f"/api/tickets/{ticket.id}", json={"tags": ["listed"]})

    rows = client.get("/api/tickets").json()

    assert next(row["tags"] for row in rows if row["id"] == ticket.id) == ["listed"]


def test_patch_rejects_an_overlong_tag(client, db_session):
    ticket = _feature(db_session, "Tag too long")
    resp = client.patch(f"/api/tickets/{ticket.id}", json={"tags": ["x" * (MAX_TAG_LENGTH + 1)]})
    assert resp.status_code == 400


# --- MCP -------------------------------------------------------------------


def test_mcp_update_ticket_writes_tags(db_session):
    ticket = _feature(db_session, "MCP tagged")
    args = normalize_tool_arguments(
        "loregarden_update_ticket",
        {"ticket_id": ticket.external_id, "tags": ["Docs", "docs", "infra"]},
    )

    payload = json.loads(execute_tool(db_session, "loregarden_update_ticket", args))

    assert payload["tags"] == ["Docs", "infra"]


def test_mcp_update_ticket_accepts_tags_as_its_only_field(db_session):
    """tags alone must satisfy the "supply at least one field" guard."""
    ticket = _feature(db_session, "MCP tags only")
    args = normalize_tool_arguments(
        "loregarden_update_ticket", {"ticket_id": ticket.external_id, "tags": ["solo"]}
    )

    payload = json.loads(execute_tool(db_session, "loregarden_update_ticket", args))

    assert payload["tags"] == ["solo"]
