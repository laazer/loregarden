"""Initiatives as a whole: every milestone, whichever workspace it lives in.

Creating, attaching, detaching and deleting go through the ordinary ticket
writers (`TicketService`, `reparent_ticket`) — the hierarchy rules live there.
This module only reads, because the one thing no ticket endpoint can do is
show an initiative with all of its milestones: each of them is scoped to one
workspace, and an initiative is bound to none.
"""

from __future__ import annotations

from loregarden.models.domain import (
    InitiativeMilestoneView,
    InitiativeProgress,
    InitiativeView,
    Ticket,
    WorkItemType,
    Workspace,
)
from loregarden.services.ticket_rollup import RESOLVED_STATES
from sqlmodel import Session, col, select


def _workspace_slugs(session: Session, tickets: list[Ticket]) -> dict[str, str]:
    ids = {t.workspace_id for t in tickets if t.workspace_id is not None}
    if not ids:
        return {}
    rows = session.exec(select(Workspace.id, Workspace.slug).where(col(Workspace.id).in_(ids)))
    return dict(rows.all())


def _milestone_view(ticket: Ticket, slugs: dict[str, str]) -> InitiativeMilestoneView:
    return InitiativeMilestoneView(
        id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        state=ticket.state,
        workspace_slug=slugs.get(ticket.workspace_id or "", ""),
    )


def _initiative_view(
    initiative: Ticket, milestones: list[Ticket], slugs: dict[str, str]
) -> InitiativeView:
    views = [_milestone_view(m, slugs) for m in milestones]
    return InitiativeView(
        id=initiative.id,
        external_id=initiative.external_id,
        title=initiative.title,
        description=initiative.description,
        state=initiative.state,
        priority=initiative.priority,
        milestones=views,
        progress=InitiativeProgress(
            resolved=sum(1 for m in milestones if m.state in RESOLVED_STATES),
            total=len(milestones),
        ),
        workspaces=sorted({v.workspace_slug for v in views if v.workspace_slug}),
    )


def _milestones_under(session: Session, parent_ids: list[str]) -> list[Ticket]:
    if not parent_ids:
        return []
    query = (
        select(Ticket)
        .where(col(Ticket.parent_ticket_id).in_(parent_ids))
        .order_by(Ticket.priority, Ticket.created_at)
    )
    return list(session.exec(query).all())


def list_initiatives(session: Session) -> list[InitiativeView]:
    """Every initiative, each with all of its milestones. Three queries, not N+1."""
    initiatives = list(
        session.exec(
            select(Ticket)
            .where(Ticket.work_item_type == WorkItemType.INITIATIVE)
            .order_by(Ticket.priority, Ticket.created_at)
        ).all()
    )
    milestones = _milestones_under(session, [i.id for i in initiatives])
    slugs = _workspace_slugs(session, milestones)
    by_parent: dict[str, list[Ticket]] = {}
    for milestone in milestones:
        by_parent.setdefault(milestone.parent_ticket_id or "", []).append(milestone)
    return [_initiative_view(i, by_parent.get(i.id, []), slugs) for i in initiatives]


def get_initiative(session: Session, initiative_id: str) -> InitiativeView:
    initiative = session.get(Ticket, initiative_id)
    if initiative is None or initiative.work_item_type != WorkItemType.INITIATIVE:
        raise LookupError(f"Initiative not found: {initiative_id}")
    milestones = _milestones_under(session, [initiative.id])
    return _initiative_view(initiative, milestones, _workspace_slugs(session, milestones))


def attachable_milestones(session: Session) -> list[InitiativeMilestoneView]:
    """Milestones in any workspace that no initiative owns yet."""
    milestones = list(
        session.exec(
            select(Ticket)
            .where(
                Ticket.work_item_type == WorkItemType.MILESTONE,
                col(Ticket.parent_ticket_id).is_(None),
            )
            .order_by(Ticket.priority, Ticket.created_at)
        ).all()
    )
    slugs = _workspace_slugs(session, milestones)
    return [_milestone_view(m, slugs) for m in milestones]
