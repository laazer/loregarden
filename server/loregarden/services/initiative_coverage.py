"""The tickets an initiative covers — the one walk every initiative reader shares.

An initiative covers two kinds of top-level ticket, its **roots**:

* its **children** — milestones, and the features or bugs a sprint parents
  directly (`VALID_HIERARCHY`);
* its **members** (`initiative_members`) — any non-initiative ticket it tracks
  without parenting it. A member keeps its parent, its milestone, its
  workspace and its integration branch; membership only says "this initiative
  tracks and schedules this ticket".

Either way a root brings its whole subtree. The roots are the plan's phases,
in one order: priority, then age. A ticket may be a member of several
initiatives, and each one shows it.

Landing, worktrees, dependency readiness and branch targeting never read this
module. They follow `parent_ticket_id` alone (`target_branch.subtree_root`), so
adding or removing a member cannot move where a ticket's work lands.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from loregarden.models.domain import InitiativeMember, Ticket, WorkItemType, comparable_utc
from loregarden.services.hierarchy_service import descendants_by_root
from sqlmodel import Session, col, select


@dataclass(frozen=True)
class InitiativeRoots:
    """An initiative's top-level tickets, and which of them are members."""

    roots: list[Ticket]
    member_ids: frozenset[str]

    def is_member(self, ticket_id: str) -> bool:
        return ticket_id in self.member_ids


@dataclass(frozen=True)
class InitiativeCoverage:
    """Every ticket an initiative covers: its roots, and each root's subtree."""

    roots: list[Ticket]
    member_ids: frozenset[str]
    #: Root id -> every ticket below it (the root itself excluded). A root that
    #: sits inside another root's subtree keeps its own tree; the outer one
    #: stops at it, so no ticket is listed twice.
    trees: dict[str, list[Ticket]]

    def tickets(self) -> list[Ticket]:
        return [*self.roots, *(t for root in self.roots for t in self.trees[root.id])]

    def ticket_ids(self) -> set[str]:
        return {t.id for t in self.tickets()}


def _order(ticket: Ticket) -> tuple[int, datetime, str]:
    return (ticket.priority, comparable_utc(ticket.created_at), ticket.id)


def member_rows(session: Session, initiative_ids: list[str]) -> list[InitiativeMember]:
    if not initiative_ids:
        return []
    return list(
        session.exec(
            select(InitiativeMember).where(col(InitiativeMember.initiative_id).in_(initiative_ids))
        ).all()
    )


def initiative_roots(session: Session, initiative_ids: list[str]) -> dict[str, InitiativeRoots]:
    """Each initiative's children and members, one order. Two queries for any number."""
    if not initiative_ids:
        return {}
    by_initiative: dict[str, dict[str, Ticket]] = defaultdict(dict)
    for child in session.exec(
        select(Ticket).where(col(Ticket.parent_ticket_id).in_(initiative_ids))
    ).all():
        by_initiative[child.parent_ticket_id or ""][child.id] = child

    members: dict[str, set[str]] = defaultdict(set)
    rows = session.exec(
        select(InitiativeMember, Ticket)
        .join(Ticket, col(Ticket.id) == col(InitiativeMember.ticket_id))
        .where(col(InitiativeMember.initiative_id).in_(initiative_ids))
    ).all()
    for row, ticket in rows:
        tickets = by_initiative[row.initiative_id]
        # Parented wins: a member later re-parented under the same initiative
        # is one root, and it is not one a "remove member" could take away.
        if ticket.id not in tickets:
            tickets[ticket.id] = ticket
            members[row.initiative_id].add(ticket.id)

    return {
        initiative_id: InitiativeRoots(
            roots=sorted(by_initiative[initiative_id].values(), key=_order),
            member_ids=frozenset(members[initiative_id]),
        )
        for initiative_id in initiative_ids
    }


def initiative_coverage(session: Session, initiative_id: str) -> InitiativeCoverage:
    """Every ticket one initiative covers, one query per tree level."""
    roots = initiative_roots(session, [initiative_id])[initiative_id]
    return InitiativeCoverage(
        roots=roots.roots,
        member_ids=roots.member_ids,
        trees=descendants_by_root(session, [r.id for r in roots.roots]),
    )


def covered_ticket_ids(session: Session, ticket_id: str) -> list[str]:
    """The ids a board scoped to ``ticket_id`` shows, below it.

    An initiative's board shows everything it covers, members included; any
    other ticket's shows its subtree.
    """
    ticket = session.get(Ticket, ticket_id)
    if ticket is not None and ticket.work_item_type == WorkItemType.INITIATIVE:
        return sorted(initiative_coverage(session, ticket_id).ticket_ids())
    return [t.id for t in descendants_by_root(session, [ticket_id])[ticket_id]]


def initiatives_tracking(session: Session, ticket_ids: list[str]) -> list[str]:
    """Initiatives holding any of ``ticket_ids`` as a member."""
    if not ticket_ids:
        return []
    return sorted(
        set(
            session.exec(
                select(InitiativeMember.initiative_id).where(
                    col(InitiativeMember.ticket_id).in_(ticket_ids)
                )
            ).all()
        )
    )


def tracked_ticket_ids(session: Session, ticket_ids: list[str]) -> set[str]:
    """Which of ``ticket_ids`` some initiative holds as a member."""
    if not ticket_ids:
        return set()
    return set(
        session.exec(
            select(InitiativeMember.ticket_id).where(
                col(InitiativeMember.ticket_id).in_(ticket_ids)
            )
        ).all()
    )


def member_initiative_ids(session: Session) -> set[str]:
    """Every initiative with at least one member."""
    return set(session.exec(select(InitiativeMember.initiative_id).distinct()).all())
