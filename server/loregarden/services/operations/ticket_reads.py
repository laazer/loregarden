"""The three views of one ticket, as operations.

Three operations rather than one shape, because they answer three different
questions: an agent's brief carries the requirement and blockers and none of
the page's artifacts, and the page's detail carries runtime, transitions and
delivery that would only crowd an agent's context.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import Ticket, TicketDetail
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_detail_view import ticket_detail
from loregarden.services.ticket_ids import TicketNotFound
from loregarden.services.ticket_ids import resolve as resolve_external_id
from loregarden.services.ticket_state_payload import ticket_brief, ticket_state_for_read
from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel import Session


class TicketRef(BaseModel):
    """A ticket by its UUID, or by its external id and (optionally) its workspace."""

    # MCP callers have always been able to send keys a tool does not read, and
    # the tool ignored them; refusing them now would break agents mid-run.
    model_config = ConfigDict(extra="ignore", use_attribute_docstrings=True)

    ticket_id: str | None = None
    """Loregarden ticket UUID or external id (e.g. lor-mcp-gateway-142)."""
    external_id: str | None = None
    """Ticket external id, e.g. lor-mcp-gateway-142, when not using ticket_id."""
    workspace_slug: str | None = None
    """Workspace slug, e.g. loregarden; required when resolving by external id."""

    @model_validator(mode="after")
    def _names_a_ticket(self) -> TicketRef:
        if not (self.ticket_id or self.external_id):
            raise ValueError("ticket_id or external_id is required")
        return self

    def resolve(self, session: Session) -> Ticket:
        return OrchestrationCallbackService(session).resolve_ticket(
            ticket_id=self.ticket_id,
            external_id=self.external_id,
            workspace_slug=self.workspace_slug,
        )


class TicketKey(BaseModel):
    """A ticket by its UUID or any spelling of its external id, as a pasted link carries."""

    ticket_id: str

    def resolve(self, session: Session) -> Ticket:
        ticket = session.get(Ticket, self.ticket_id) or resolve_external_id(session, self.ticket_id)
        if ticket is None:
            raise TicketNotFound(self.ticket_id)
        return ticket


def _brief(session: Session, ref: TicketRef, _caller: Caller) -> dict[str, Any]:
    return ticket_brief(session, ref.resolve(session))


def _state(session: Session, ref: TicketRef, _caller: Caller) -> dict[str, Any]:
    return ticket_state_for_read(session, ref.resolve(session))


def _detail(session: Session, key: TicketKey, _caller: Caller) -> TicketDetail:
    return ticket_detail(session, key.resolve(session))


TICKET_READS = (
    Operation(
        id=OperationId.TICKET_BRIEF,
        effect=Effect.READ,
        args=TicketRef,
        handler=_brief,
        unavailable_on={
            Transport.REST: "no REST caller: the ticket page reads ticket_detail",
        },
    ),
    Operation(
        id=OperationId.TICKET_STATE,
        effect=Effect.READ,
        args=TicketRef,
        handler=_state,
        unavailable_on={
            Transport.MCP: "every MCP write tool already replies with it, and the read "
            "tools return the brief, which contains it",
            Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
        },
    ),
    Operation(
        id=OperationId.TICKET_DETAIL,
        # It reconciles the ticket and backfills missing diff and test
        # artifacts on the way (`ticket_detail_view.ticket_detail`).
        effect=Effect.WRITE,
        args=TicketKey,
        handler=_detail,
        unavailable_on={
            Transport.MCP: "the page's payload: every artifact and runtime setting, which "
            "would crowd an agent's context; agents read ticket_brief",
            Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
        },
    ),
)
