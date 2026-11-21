"""The handoff feed: what each agent passed the next, for the Baxter dock's Handoffs tab."""

from __future__ import annotations

from loregarden.services.handoff_feed import MAX_LIMIT, HandoffFeed, handoff_feed
from loregarden.services.operations.spec import Caller, Effect, Operation, OperationId, Transport
from loregarden.services.operations.ticket_reads import TicketKey
from loregarden.services.organization_gate_service import workspace_for_slug
from pydantic import BaseModel, Field
from sqlmodel import Session


class HandoffFeedArgs(BaseModel):
    ticket_id: str | None = None
    """One ticket's handoffs, by UUID or external id. Takes precedence over `workspace`."""
    workspace: str | None = None
    """One workspace's handoffs, by slug. Neither given: every workspace's."""
    q: str = Field("", max_length=200)
    """Only handoffs whose agents, ticket id or ticket title contain this, ignoring case."""
    limit: int = Field(50, ge=1, le=MAX_LIMIT)


def _handoff_feed(session: Session, args: HandoffFeedArgs, _caller: Caller) -> HandoffFeed:
    if args.ticket_id is not None:
        ticket = TicketKey(ticket_id=args.ticket_id).resolve(session)
        return handoff_feed(session, ticket_id=ticket.id, search=args.q, limit=args.limit)
    workspace = workspace_for_slug(session, args.workspace) if args.workspace is not None else None
    return handoff_feed(session, workspace=workspace, search=args.q, limit=args.limit)


HANDOFF_OPERATIONS = (
    Operation(
        id=OperationId.HANDOFF_FEED,
        effect=Effect.READ,
        args=HandoffFeedArgs,
        handler=_handoff_feed,
        unavailable_on={
            Transport.MCP: "a stage inherits its predecessor's handoff through the transition "
            "itself (stage_verdict, handoff_boundary); the feed is a person reading the chain",
            Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
        },
    ),
)
