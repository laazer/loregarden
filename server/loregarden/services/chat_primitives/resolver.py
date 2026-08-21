"""Validate thin refs on chat primitives and attach title fallbacks.

Agents emit refs (ticket_id, agent_id, …). The client then renders live state
from existing APIs; this layer only checks the ref resolves and fills a
display title so a broken link can degrade gracefully.

Resolution is confined to the chat's workspace. `external_id` numbers from 1
within each workspace (see `services.ticket_ids`), so an unscoped lookup would
resolve a colliding ref to an arbitrary workspace.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket
from loregarden.models.domain.chat_primitives import (
    AgentPart,
    ChatPart,
    FilterableKanbanPart,
    GatePart,
    KanbanPart,
    ParentTicketPart,
    StatusColumnPart,
    TicketListPart,
    TicketPart,
    TicketWorkflowPart,
    WorkflowPart,
)
from loregarden.services.ticket_ids import resolve as resolve_external_id
from sqlmodel import Session


def _resolve_ticket_id(
    session: Session, ticket_id: str, workspace_id: str
) -> tuple[str, str | None]:
    """Return (canonical_id, title). Refs outside the workspace keep the original id."""
    ticket = session.get(Ticket, ticket_id)
    if ticket is not None and ticket.workspace_id != workspace_id:
        ticket = None
    if ticket is None:
        ticket = resolve_external_id(session, ticket_id, workspace_id=workspace_id)
    if ticket is None:
        return ticket_id, None
    return ticket.id, ticket.title or ticket.external_id or ticket.id


def resolve_parts(session: Session, parts: list[ChatPart], *, workspace_id: str) -> list[ChatPart]:
    """Return a new list with refs canonicalized and titles filled where possible."""
    resolved: list[ChatPart] = []
    for part in parts:
        if isinstance(part, (TicketPart, TicketWorkflowPart, ParentTicketPart)):
            canonical, title = _resolve_ticket_id(session, part.ticket_id, workspace_id)
            resolved.append(
                part.model_copy(
                    update={
                        "ticket_id": canonical,
                        "title": part.title or title,
                    }
                )
            )
        elif isinstance(part, TicketListPart):
            ids: list[str] = []
            for tid in part.ticket_ids:
                canonical, _ = _resolve_ticket_id(session, tid, workspace_id)
                ids.append(canonical)
            parent_id = part.parent_ticket_id
            parent_title = part.title
            if parent_id:
                parent_id, parent_title = _resolve_ticket_id(session, parent_id, workspace_id)
                parent_title = part.title or parent_title
            resolved.append(
                part.model_copy(
                    update={
                        "ticket_ids": ids,
                        "parent_ticket_id": parent_id,
                        "title": parent_title,
                    }
                )
            )
        elif isinstance(part, (StatusColumnPart, KanbanPart, FilterableKanbanPart)):
            ids = []
            for tid in part.ticket_ids:
                canonical, _ = _resolve_ticket_id(session, tid, workspace_id)
                ids.append(canonical)
            resolved.append(part.model_copy(update={"ticket_ids": ids}))
        elif isinstance(part, GatePart) and part.ticket_id:
            canonical, title = _resolve_ticket_id(session, part.ticket_id, workspace_id)
            resolved.append(
                part.model_copy(
                    update={
                        "ticket_id": canonical,
                        "title": part.title or title,
                    }
                )
            )
        elif isinstance(part, (AgentPart, WorkflowPart)):
            # Definition refs are validated lazily by the studio preview endpoints.
            resolved.append(part)
        else:
            resolved.append(part)
    return resolved
