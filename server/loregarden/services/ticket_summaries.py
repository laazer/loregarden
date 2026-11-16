"""The board's ticket list: filtered `TicketSummary` rows, cheapest state first.

Moved out of `api/tickets.py` so the `ticket_summaries` operation can run it;
the tree and status-summary routes share the workspace and filter helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loregarden.models.domain import (
    BlockedOn,
    Ticket,
    TicketActivity,
    TicketState,
    TicketSummary,
    WorkItemType,
    Workspace,
)
from loregarden.services.agent_retry import spent_agent_blocks
from loregarden.services.blocked_on import StuckPage, blocked_on_clause
from loregarden.services.hierarchy_service import child_counts
from loregarden.services.initiative_coverage import covered_ticket_ids
from loregarden.services.ticket_activity import classify_ticket_activity
from loregarden.services.ticket_detail_view import latest_run_codes, ticket_summary
from loregarden.services.workflow_service import prime_workflow_instances, stage_resolution_memo
from sqlmodel import Session, col, select


@dataclass(frozen=True)
class WorkspaceScope:
    """Which workspace a list is narrowed to.

    `unknown` is a slug that names no workspace, which lists nothing; a missing
    slug lists every workspace.
    """

    workspace: Workspace | None = None
    unknown: bool = False


def workspace_scope(session: Session, slug: str | None) -> WorkspaceScope:
    if not slug:
        return WorkspaceScope()
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    return WorkspaceScope(workspace=ws, unknown=ws is None)


def apply_ticket_query_filters(
    session: Session,
    query: Any,
    *,
    states: list[TicketState] | None,
    work_item_types: list[WorkItemType] | None,
    milestone: str | None,
    search: str | None,
    blocked_on: list[BlockedOn] | None = None,
) -> Any:
    if states:
        query = query.where(col(Ticket.state).in_(states))
    if blocked_on:
        query = query.where(blocked_on_clause(blocked_on, spent_agent_blocks(session)))
    if work_item_types:
        query = query.where(col(Ticket.work_item_type).in_(work_item_types))
    if milestone:
        query = query.where(Ticket.milestone == milestone)
    if search:
        term = f"%{search.strip()}%"
        query = query.where((col(Ticket.title).like(term)) | (col(Ticket.external_id).like(term)))
    return query


@dataclass(frozen=True)
class SummaryFilters:
    workspace: str | None = None
    states: list[TicketState] | None = None
    work_item_types: list[WorkItemType] | None = None
    parent_ticket_id: str | None = None
    ancestor_ticket_id: str | None = None
    roots_only: bool = False
    blocked_on: list[BlockedOn] | None = None
    milestone: str | None = None
    search: str | None = None
    limit: int | None = None
    offset: int = 0


def ticket_summaries(session: Session, filters: SummaryFilters) -> list[TicketSummary]:
    """Ticket summaries matching `filters`, cheapest state first.

    `limit` is opt-in and unset by default. Callers ask for a whole filtered
    set and count it themselves (the children of a parent, every dispatchable
    ticket), so a default page size would silently truncate them into
    answering a different question. `offset` without `limit` is refused.
    """
    if filters.limit is None and filters.offset:
        raise ValueError("offset requires limit")

    scope = workspace_scope(session, filters.workspace)
    if scope.unknown:
        return []

    query = select(Ticket)
    if scope.workspace:
        query = query.where(Ticket.workspace_id == scope.workspace.id)
    query = apply_ticket_query_filters(
        session,
        query,
        states=filters.states,
        work_item_types=filters.work_item_types,
        milestone=filters.milestone,
        search=filters.search,
        blocked_on=filters.blocked_on,
    )
    if filters.parent_ticket_id:
        query = query.where(Ticket.parent_ticket_id == filters.parent_ticket_id)
    if filters.roots_only:
        query = query.where(col(Ticket.parent_ticket_id).is_(None))
    if filters.ancestor_ticket_id:
        # Every level below it, in whichever workspaces they live: an
        # initiative's board spans repositories, and shows its members too.
        covered = covered_ticket_ids(session, filters.ancestor_ticket_id)
        query = query.where(col(Ticket.id).in_(covered))
    query = query.order_by(Ticket.priority, Ticket.created_at)
    if filters.limit is not None:
        # Paged in SQL, not after the fact: the cost here is building a summary
        # per row, and slicing a materialised list would pay it for every row.
        query = query.offset(filters.offset).limit(filters.limit)
    return _summaries(session, list(session.exec(query).all()))


def _summaries(session: Session, tickets: list[Ticket]) -> list[TicketSummary]:
    ticket_ids = [t.id for t in tickets]
    activity = classify_ticket_activity(session, ticket_ids)
    run_codes = latest_run_codes(session, ticket_ids)
    children = child_counts(session, ticket_ids)
    stuck = StuckPage.of(session, tickets)
    with stage_resolution_memo():
        prime_workflow_instances(session, ticket_ids)
        return [
            ticket_summary(
                session,
                t,
                activity.get(t.id, TicketActivity.IDLE),
                run_code=run_codes.get(t.id, ""),
                stuck=stuck,
                children=children.get(t.id, 0),
            )
            for t in tickets
        ]
