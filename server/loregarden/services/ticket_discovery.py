"""Ticket discovery helpers for MCP tools and agent run context."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlmodel import Session, col, select

from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def looks_like_ticket_uuid(value: str) -> bool:
    text = value.strip()
    if not _UUID_RE.match(text):
        return False
    try:
        UUID(text)
    except ValueError:
        return False
    return True


def _workspace_by_slug(session: Session, workspace_slug: str) -> Workspace | None:
    return session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()


def compact_ticket_row(session: Session, ticket: Ticket) -> dict[str, Any]:
    ws = session.get(Workspace, ticket.workspace_id)
    return {
        "id": ticket.id,
        "external_id": ticket.external_id,
        "title": ticket.title,
        "state": ticket.state.value if hasattr(ticket.state, "value") else str(ticket.state),
        "work_item_type": ticket.work_item_type.value
        if hasattr(ticket.work_item_type, "value")
        else str(ticket.work_item_type),
        "workspace_slug": ws.slug if ws else "",
        "workflow_stage_key": ticket.workflow_stage_key,
        "workflow_stage_status": ticket.workflow_stage_status.value
        if hasattr(ticket.workflow_stage_status, "value")
        else str(ticket.workflow_stage_status),
        "parent_ticket_id": ticket.parent_ticket_id or "",
        "priority": ticket.priority,
    }


def list_tickets_mcp(
    session: Session,
    *,
    workspace_slug: str,
    state: str | None = None,
    work_item_type: str | None = None,
    search: str | None = None,
    parent_ticket_id: str | None = None,
    parent_external_id: str | None = None,
    roots_only: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    ws = _workspace_by_slug(session, workspace_slug)
    if not ws:
        return {"workspace_slug": workspace_slug, "count": 0, "tickets": []}

    query = select(Ticket).where(Ticket.workspace_id == ws.id)

    if state:
        query = query.where(Ticket.state == TicketState(state))
    if work_item_type:
        query = query.where(Ticket.work_item_type == WorkItemType(work_item_type))
    if parent_ticket_id:
        query = query.where(Ticket.parent_ticket_id == parent_ticket_id)
    if parent_external_id:
        parent = session.exec(
            select(Ticket).where(
                Ticket.workspace_id == ws.id,
                Ticket.external_id == parent_external_id,
            )
        ).first()
        if not parent:
            return {"workspace_slug": workspace_slug, "count": 0, "tickets": []}
        query = query.where(Ticket.parent_ticket_id == parent.id)
    if roots_only:
        query = query.where(Ticket.parent_ticket_id.is_(None))  # type: ignore[union-attr]
    if search:
        term = f"%{search.strip()}%"
        query = query.where((col(Ticket.title).like(term)) | (col(Ticket.external_id).like(term)))

    cap = max(1, min(limit, 100))
    tickets = session.exec(query.order_by(Ticket.priority, Ticket.created_at).limit(cap)).all()
    rows = [compact_ticket_row(session, ticket) for ticket in tickets]
    return {"workspace_slug": workspace_slug, "count": len(rows), "tickets": rows}


def ticket_neighbors_mcp(session: Session, ticket: Ticket) -> dict[str, Any]:
    """Parent, siblings, and direct children for the active ticket."""
    parent = None
    siblings: list[dict[str, Any]] = []
    if ticket.parent_ticket_id:
        parent_ticket = session.get(Ticket, ticket.parent_ticket_id)
        if parent_ticket:
            parent = compact_ticket_row(session, parent_ticket)
            siblings = [
                compact_ticket_row(session, sibling)
                for sibling in session.exec(
                    select(Ticket).where(
                        Ticket.parent_ticket_id == parent_ticket.id,
                        Ticket.id != ticket.id,
                    )
                ).all()
            ]

    children = [
        compact_ticket_row(session, child)
        for child in session.exec(
            select(Ticket).where(Ticket.parent_ticket_id == ticket.id).order_by(Ticket.priority)
        ).all()
    ]

    return {
        "self": compact_ticket_row(session, ticket),
        "parent": parent,
        "siblings": siblings,
        "children": children,
    }
