"""Lists of tickets and of a ticket's artifacts, as operations.

Two of each, because the agent and the page ask different questions. An agent
looking a ticket up wants short rows it can choose from (`ticket_search`); the
board wants full summaries it can filter and page (`ticket_summaries`). An
agent wants to know which documents describe a ticket, up its ancestors and
without bodies (`artifact_index`); the Artifacts tab wants every row on one
ticket as it lands, content included (`artifact_feed`).
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import BlockedOn, TicketState, TicketSummary, WorkItemType
from loregarden.services.artifact_service import get_ticket_artifact, list_ticket_artifacts
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.operations.ticket_reads import TicketKey
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_discovery import list_tickets_mcp
from loregarden.services.ticket_documents import MAX_ARTIFACT_LIST_LIMIT, list_artifacts
from loregarden.services.ticket_summaries import SummaryFilters, ticket_summaries
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True)


class TicketSearch(BaseModel):
    model_config = _LENIENT

    #: Empty lists initiatives across workspaces; otherwise required, and
    #: `list_tickets_mcp` says so.
    workspace_slug: str = ""
    """Workspace slug, e.g. loregarden. Required unless listing initiatives."""
    state: TicketState | None = None
    """Optional ticket state filter."""
    work_item_type: WorkItemType | None = None
    """Optional work item type filter."""
    search: str | None = None
    """Optional title or external_id substring search."""
    parent_ticket_id: str | None = None
    """Optional parent ticket UUID."""
    parent_external_id: str | None = None
    """Optional parent external id."""
    roots_only: bool = False
    """Only top-level tickets (no parent)."""
    #: Clamped to 1..100 by `list_tickets_mcp`: an agent narrows, it does not page.
    limit: int = 50
    """Max results (default 50, max 100)."""


class TicketSummaryQuery(BaseModel):
    workspace: str | None = None
    states: list[TicketState] | None = None
    work_item_types: list[WorkItemType] | None = None
    parent_ticket_id: str | None = None
    ancestor_ticket_id: str | None = None
    roots_only: bool = False
    blocked_on: list[BlockedOn] | None = None
    milestone: str | None = None
    search: str | None = None
    limit: int | None = Field(default=None, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ArtifactIndexQuery(BaseModel):
    model_config = _LENIENT

    ticket_id: str
    """Ticket UUID or external id (initiatives and milestones included)."""
    workspace_slug: str | None = None
    """Workspace slug — only needed to disambiguate a legacy external id."""
    #: Artifact kinds are agent-supplied and open-ended (see ArtifactKind).
    kind: str = ""
    """Only artifacts of this kind, e.g. document, plan, log."""
    include_ancestors: bool = True
    """Also list the parent chain's artifacts (default true)."""
    latest_only: bool = True
    """Collapse each ticket + kind + title to its newest version (default true)."""
    limit: int = Field(default=50, ge=1, le=MAX_ARTIFACT_LIST_LIMIT)
    """Max items (default 50, max 200)."""


class ArtifactFeedQuery(TicketKey):
    limit: int = Field(default=200, ge=1)


class ArtifactDetailQuery(TicketKey):
    artifact_id: str


class ArtifactNotFound(LookupError):  # noqa: N818 — reads as the condition
    """No artifact of that id belongs to that ticket."""


def _search(session: Session, query: TicketSearch, _caller: Caller) -> dict[str, Any]:
    return list_tickets_mcp(
        session,
        workspace_slug=query.workspace_slug,
        state=query.state,
        work_item_type=query.work_item_type,
        search=query.search,
        parent_ticket_id=query.parent_ticket_id,
        parent_external_id=query.parent_external_id,
        roots_only=query.roots_only,
        limit=query.limit,
    )


def _summaries(session: Session, query: TicketSummaryQuery, _caller: Caller) -> list[TicketSummary]:
    return ticket_summaries(session, SummaryFilters(**query.model_dump()))


def _index(session: Session, query: ArtifactIndexQuery, _caller: Caller) -> dict[str, Any]:
    ticket = OrchestrationCallbackService(session).resolve_ticket(
        ticket_id=query.ticket_id, workspace_slug=query.workspace_slug
    )
    return list_artifacts(
        session,
        ticket,
        kind=query.kind,
        include_ancestors=query.include_ancestors,
        latest_only=query.latest_only,
        limit=query.limit,
    )


def _feed(session: Session, query: ArtifactFeedQuery, _caller: Caller) -> dict[str, Any]:
    return list_ticket_artifacts(session, query.resolve(session).id, limit=query.limit)


def _artifact(session: Session, query: ArtifactDetailQuery, _caller: Caller) -> dict[str, Any]:
    # By the ticket's own id: an artifact of another ticket is not served
    # under this one's path.
    item = get_ticket_artifact(session, query.resolve(session).id, query.artifact_id)
    if item is None:
        raise ArtifactNotFound("Artifact not found")
    return item


TICKET_LISTS = (
    Operation(
        id=OperationId.TICKET_SEARCH,
        effect=Effect.READ,
        args=TicketSearch,
        handler=_search,
        unavailable_on={Transport.REST: "no REST caller: the board reads ticket_summaries"},
    ),
    Operation(
        id=OperationId.TICKET_SUMMARIES,
        effect=Effect.READ,
        args=TicketSummaryQuery,
        handler=_summaries,
        unavailable_on={
            Transport.MCP: "the board's rows: activity, runtime and delivery per ticket, which "
            "would crowd an agent's context; agents read ticket_search",
            Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
        },
    ),
    Operation(
        id=OperationId.ARTIFACT_INDEX,
        effect=Effect.READ,
        args=ArtifactIndexQuery,
        handler=_index,
        unavailable_on={Transport.REST: "no REST caller: the Artifacts tab reads artifact_feed"},
    ),
    Operation(
        id=OperationId.ARTIFACT_FEED,
        effect=Effect.READ,
        args=ArtifactFeedQuery,
        handler=_feed,
        unavailable_on={
            Transport.MCP: "every artifact's full content, newest first, for the page to "
            "poll; agents read artifact_index, then one artifact by id",
            Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
        },
    ),
    Operation(
        id=OperationId.ARTIFACT_DETAIL,
        effect=Effect.READ,
        args=ArtifactDetailQuery,
        handler=_artifact,
        unavailable_on={
            Transport.MCP: "the whole of one artifact for the Artifacts tab; agents read "
            "an artifact through artifact_index and the document tools",
            Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
        },
    ),
)
