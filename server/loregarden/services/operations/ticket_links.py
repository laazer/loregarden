"""The edges between tickets, as operations: a prerequisite and a relation, each
added and removed.

These were written twice, as MCP tools and as routes under
`/api/tickets/{id}/`, and the routes had drifted: their removals took the other
ticket's id raw, so a route given an external id deleted nothing and answered
200, and every route found the ticket itself by UUID alone. Each operation now
resolves both tickets the one way, on every transport.

The edit is one; the reply is the transport's. An agent has always been
answered with the ticket's state payload, and the ticket page with its detail.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from loregarden.models.domain import Ticket
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.ticket_detail_view import ticket_detail
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_state_payload import ticket_state_payload
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True)


class DependencyEdge(BaseModel):
    """``ticket_id`` waits for ``depends_on``; each a UUID or an external id."""

    model_config = _LENIENT

    ticket_id: str
    """The dependent ticket (UUID or external_id)."""
    depends_on: str
    """The prerequisite ticket (UUID or external_id)."""


class RelationEdge(BaseModel):
    """``ticket_id`` and ``related_to`` are related; each a UUID or an external id."""

    model_config = _LENIENT

    ticket_id: str
    """One ticket (UUID or external_id)."""
    related_to: str
    """The other ticket (UUID or external_id)."""


def _both(session: Session, ticket_id: str, other_id: str) -> tuple[Ticket, Ticket]:
    resolver = OrchestrationCallbackService(session)
    return resolver.resolve_ticket(ticket_id=ticket_id), resolver.resolve_ticket(ticket_id=other_id)


def _author(caller: Caller) -> str:
    """Who drew the edge, as the ticket page shows it."""
    return "user" if caller.transport is Transport.REST else "agent"


def _add_dependency(session: Session, edge: DependencyEdge, caller: Caller) -> Ticket:
    ticket, prerequisite = _both(session, edge.ticket_id, edge.depends_on)
    TicketDependencyService(session).add_dependency(
        ticket.id, prerequisite.id, created_by=_author(caller)
    )
    return ticket


def _remove_dependency(session: Session, edge: DependencyEdge, _caller: Caller) -> Ticket:
    ticket, prerequisite = _both(session, edge.ticket_id, edge.depends_on)
    # Idempotent: an edge that is not there is already the state asked for, and
    # the reply shows the prerequisites as they now stand.
    TicketDependencyService(session).remove_dependency(ticket.id, prerequisite.id)
    return ticket


def _add_relation(session: Session, edge: RelationEdge, caller: Caller) -> Ticket:
    ticket, other = _both(session, edge.ticket_id, edge.related_to)
    TicketRelationService(session).add_relation(ticket.id, other.id, created_by=_author(caller))
    return ticket


def _remove_relation(session: Session, edge: RelationEdge, _caller: Caller) -> Ticket:
    ticket, other = _both(session, edge.ticket_id, edge.related_to)
    # Idempotent, as for a prerequisite.
    TicketRelationService(session).remove_relation(ticket.id, other.id)
    return ticket


def _state_reply(session: Session, ticket: Ticket) -> dict[str, Any]:
    return ticket_state_payload(session, ticket.id)


#: An agent's reply is the state payload every MCP write returns; the page's is
#: the detail it renders.
_TICKET_REPLIES: Mapping[Transport, Callable[[Session, Any], Any]] = {
    Transport.MCP: _state_reply,
    Transport.CLI: _state_reply,
    Transport.REST: ticket_detail,
}

TICKET_LINKS = (
    Operation(
        id=OperationId.TICKET_DEPENDENCY_ADD,
        effect=Effect.WRITE,
        args=DependencyEdge,
        handler=_add_dependency,
        replies=_TICKET_REPLIES,
    ),
    Operation(
        id=OperationId.TICKET_DEPENDENCY_REMOVE,
        effect=Effect.WRITE,
        args=DependencyEdge,
        handler=_remove_dependency,
        replies=_TICKET_REPLIES,
    ),
    Operation(
        id=OperationId.TICKET_RELATION_ADD,
        effect=Effect.WRITE,
        args=RelationEdge,
        handler=_add_relation,
        replies=_TICKET_REPLIES,
    ),
    Operation(
        id=OperationId.TICKET_RELATION_REMOVE,
        effect=Effect.WRITE,
        args=RelationEdge,
        handler=_remove_relation,
        replies=_TICKET_REPLIES,
    ),
)
