"""Initiatives as a whole: every milestone, whichever workspace it lives in.

Creating, attaching, detaching and deleting go through the ordinary ticket
writers (`TicketService`, `reparent_ticket`) — the hierarchy rules live there;
members are added and removed by `initiative_membership`. This module only
reads, because the one thing no ticket endpoint can do is show an initiative
with all of its milestones: each of them is scoped to one workspace, and an
initiative is bound to none. What an initiative covers is
`initiative_coverage`'s answer.
"""

from __future__ import annotations

from loregarden.models.domain import (
    InitiativeMilestoneView,
    InitiativeProgress,
    InitiativeView,
    ScheduleTarget,
    Ticket,
    WorkItemType,
    Workspace,
)
from loregarden.services.initiative_coverage import (
    InitiativeRoots,
    initiative_roots,
    tracked_ticket_ids,
)
from loregarden.services.ticket_rollup import RESOLVED_STATES
from sqlmodel import Session, col, select


def workspace_slugs(session: Session, tickets: list[Ticket]) -> dict[str, str]:
    ids = {t.workspace_id for t in tickets if t.workspace_id is not None}
    if not ids:
        return {}
    rows = session.exec(select(Workspace.id, Workspace.slug).where(col(Workspace.id).in_(ids)))
    return dict(rows.all())


def home_milestones(session: Session, tickets: list[Ticket]) -> dict[str, Ticket]:
    """Ticket id -> the nearest milestone above it; absent when there is none.

    Where a member lives in its own tree. One query per level for the batch.
    """
    found: dict[str, Ticket] = {}
    loaded: dict[str, Ticket] = {}
    pending = {t.id: t.parent_ticket_id for t in tickets if t.parent_ticket_id}
    seen: dict[str, set[str]] = {ticket_id: {ticket_id} for ticket_id in pending}
    while pending:
        wanted = {pid for pid in pending.values() if pid and pid not in loaded}
        if wanted:
            for row in session.exec(select(Ticket).where(col(Ticket.id).in_(wanted))).all():
                loaded[row.id] = row
        following: dict[str, str | None] = {}
        for ticket_id, parent_id in pending.items():
            parent = loaded.get(parent_id or "")
            if parent is None or parent.id in seen[ticket_id]:
                continue  # dangling parent, or a parent loop: no milestone above
            if parent.work_item_type == WorkItemType.MILESTONE:
                found[ticket_id] = parent
                continue
            seen[ticket_id].add(parent.id)
            if parent.parent_ticket_id:
                following[ticket_id] = parent.parent_ticket_id
        pending = following
    return found


def milestone_view(
    ticket: Ticket, slugs: dict[str, str], *, member: bool, home: Ticket | None
) -> InitiativeMilestoneView:
    return InitiativeMilestoneView(
        id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        state=ticket.state,
        workspace_slug=slugs.get(ticket.workspace_id or "", ""),
        work_item_type=ticket.work_item_type,
        member=member,
        home_milestone=home.external_id if home is not None else "",
    )


def _initiative_view(
    initiative: Ticket,
    roots: InitiativeRoots,
    slugs: dict[str, str],
    homes: dict[str, Ticket],
) -> InitiativeView:
    views = [
        milestone_view(
            m,
            slugs,
            member=roots.is_member(m.id),
            home=homes.get(m.id) if roots.is_member(m.id) else None,
        )
        for m in roots.roots
    ]
    return InitiativeView(
        id=initiative.id,
        external_id=initiative.external_id,
        title=initiative.title,
        description=initiative.description,
        state=initiative.state,
        priority=initiative.priority,
        milestones=views,
        progress=InitiativeProgress(
            resolved=sum(1 for m in roots.roots if m.state in RESOLVED_STATES),
            total=len(roots.roots),
        ),
        workspaces=sorted({v.workspace_slug for v in views if v.workspace_slug}),
    )


def _initiative_views(session: Session, initiatives: list[Ticket]) -> list[InitiativeView]:
    """Views for many initiatives in a fixed number of queries, not N+1."""
    by_initiative = initiative_roots(session, [i.id for i in initiatives])
    every_root = [t for roots in by_initiative.values() for t in roots.roots]
    members = [t for roots in by_initiative.values() for t in roots.roots if roots.is_member(t.id)]
    slugs = workspace_slugs(session, every_root)
    homes = home_milestones(session, members)
    return [_initiative_view(i, by_initiative[i.id], slugs, homes) for i in initiatives]


def milestones_under(session: Session, initiative_id: str) -> list[Ticket]:
    """The initiative's phases: its children and its members (`initiative_coverage`)."""
    return initiative_roots(session, [initiative_id])[initiative_id].roots


def list_initiatives(session: Session) -> list[InitiativeView]:
    """Every initiative, each with all of its milestones and members."""
    initiatives = list(
        session.exec(
            select(Ticket)
            .where(Ticket.work_item_type == WorkItemType.INITIATIVE)
            .order_by(Ticket.priority, Ticket.created_at)
        ).all()
    )
    return _initiative_views(session, initiatives)


def get_initiative(session: Session, initiative_id: str) -> InitiativeView:
    return _initiative_views(session, [load_initiative(session, initiative_id)])[0]


def attachable_milestones(session: Session) -> list[InitiativeMilestoneView]:
    """Milestones in any workspace that no initiative parents or tracks yet."""
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
    tracked = tracked_ticket_ids(session, [m.id for m in milestones])
    milestones = [m for m in milestones if m.id not in tracked]
    slugs = workspace_slugs(session, milestones)
    return [milestone_view(m, slugs, member=False, home=None) for m in milestones]


def load_initiative(session: Session, initiative_id: str) -> Ticket:
    initiative = session.get(Ticket, initiative_id)
    if initiative is None or initiative.work_item_type != WorkItemType.INITIATIVE:
        raise LookupError(f"Initiative not found: {initiative_id}")
    return initiative


def load_targets(session: Session, ticket_ids: list[str]) -> dict[str, ScheduleTarget]:
    if not ticket_ids:
        return {}
    rows = session.exec(select(ScheduleTarget).where(col(ScheduleTarget.ticket_id).in_(ticket_ids)))
    return {row.ticket_id: row for row in rows.all()}
