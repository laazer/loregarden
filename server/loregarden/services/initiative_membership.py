"""Adding a ticket to an initiative, and taking it out, without re-parenting it.

Re-parenting a ticket under an initiative moves it out of its milestone's tree,
and with it the integration branch its work lands on (`target_branch`): work
already landed on the old branch stays behind. Membership leaves the tree
alone. What an initiative then covers is `initiative_coverage`'s to answer.

The rules, each refused with its own message:

* any type but an initiative may be a member, and brings its subtree;
* a ticket the same initiative already covers — as a member, by parenting, or
  under a member or child — is refused rather than tracked twice;
* a ticket whose subtree holds one of the initiative's members is refused too:
  remove the member first, so the plan has one phase for that work, not two;
* a ticket whose parent chain loops back on itself is refused: membership
  would bring an endless subtree.

A ticket may belong to several initiatives; each one shows and schedules it.
"""

from __future__ import annotations

from loregarden.models.domain import (
    InitiativeMember,
    InitiativeMilestoneView,
    Ticket,
    WorkItemType,
)
from loregarden.services.hierarchy_service import descendants_by_root
from loregarden.services.initiative_coverage import initiative_coverage, initiative_roots
from loregarden.services.initiative_service import (
    home_milestones,
    load_initiative,
    milestone_view,
    workspace_slugs,
)
from loregarden.services.ticket_rollup import reconcile_lineage
from loregarden.services.ticket_state_service import RESOLVED_STATES
from sqlmodel import Session, col, select

#: Most rows the "add a ticket" search returns.
CANDIDATE_LIMIT = 15


class InitiativeMembershipError(ValueError):
    """A membership change the rules refuse (an initiative, a parent loop)."""


class MembershipConflictError(InitiativeMembershipError):
    """The initiative already covers the ticket, or the ticket holds a member."""


def _ancestors(session: Session, ticket: Ticket) -> list[Ticket]:
    """Parent first, root last. Raises on a parent loop."""
    chain: list[Ticket] = []
    seen = {ticket.id}
    parent_id = ticket.parent_ticket_id
    while parent_id:
        if parent_id in seen:
            raise InitiativeMembershipError(
                f"{ticket.external_id}'s parent chain loops back through {parent_id}; "
                "fix its parent before an initiative tracks it."
            )
        seen.add(parent_id)
        parent = session.get(Ticket, parent_id)
        if parent is None:
            break
        chain.append(parent)
        parent_id = parent.parent_ticket_id
    return chain


def _load_ticket(session: Session, ticket_id: str) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise LookupError(f"Ticket not found: {ticket_id}")
    return ticket


def _check_addable(session: Session, initiative: Ticket, ticket: Ticket) -> None:
    if ticket.work_item_type == WorkItemType.INITIATIVE:
        raise InitiativeMembershipError(
            f"{ticket.external_id} is an initiative; an initiative cannot be a member of another."
        )
    roots = initiative_roots(session, [initiative.id])[initiative.id]
    if roots.is_member(ticket.id):
        raise MembershipConflictError(
            f"{ticket.external_id} is already a member of {initiative.external_id}."
        )
    if ticket.parent_ticket_id == initiative.id:
        raise MembershipConflictError(
            f"{ticket.external_id} is already a child of {initiative.external_id}."
        )
    ancestors = _ancestors(session, ticket)
    root_ids = {r.id for r in roots.roots}
    for ancestor in ancestors:
        if ancestor.id == initiative.id or ancestor.id in root_ids:
            how = "a member" if roots.is_member(ancestor.id) else "a child"
            raise MembershipConflictError(
                f"{initiative.external_id} already covers {ticket.external_id} through "
                f"{ancestor.external_id}, {how} of it."
            )
    below = descendants_by_root(session, [ticket.id])[ticket.id]
    if any(t.id == initiative.id for t in below):
        raise InitiativeMembershipError(
            f"{initiative.external_id} sits under {ticket.external_id}; tracking it would "
            "make the initiative a member of itself."
        )
    held = sorted(t.external_id for t in below if t.id in root_ids)
    if held:
        raise MembershipConflictError(
            f"{ticket.external_id} contains {', '.join(held)}, already in "
            f"{initiative.external_id}; remove {'it' if len(held) == 1 else 'them'} first."
        )


def add_member(
    session: Session, initiative_id: str, ticket_id: str, *, actor: str
) -> InitiativeMember:
    """Track ``ticket_id`` (and its subtree) in the initiative. Touches no ticket."""
    initiative = load_initiative(session, initiative_id)
    ticket = _load_ticket(session, ticket_id)
    _check_addable(session, initiative, ticket)
    row = InitiativeMember(initiative_id=initiative.id, ticket_id=ticket.id, added_by=actor)
    session.add(row)
    session.commit()
    # The initiative's state is a summary of what it covers, which just grew.
    reconcile_lineage(session, initiative.id)
    return row


def remove_member(session: Session, initiative_id: str, ticket_id: str) -> None:
    """Stop tracking ``ticket_id``. The ticket itself is not touched."""
    initiative = load_initiative(session, initiative_id)
    row = session.get(InitiativeMember, (initiative.id, ticket_id))
    if row is None:
        ticket = session.get(Ticket, ticket_id)
        name = ticket.external_id if ticket is not None else ticket_id
        if ticket is not None and ticket.parent_ticket_id == initiative.id:
            raise LookupError(
                f"{name} is a child of {initiative.external_id}, not a member; "
                "detach it or move it to a milestone instead."
            )
        raise LookupError(f"{name} is not a member of {initiative.external_id}.")
    session.delete(row)
    session.commit()
    reconcile_lineage(session, initiative.id)


def member_candidates(
    session: Session, initiative_id: str, search: str
) -> list[InitiativeMilestoneView]:
    """Tickets matching ``search`` that the initiative could start tracking.

    Any type but an initiative, in any workspace, open work first. What the
    initiative already covers is left out.
    """
    initiative = load_initiative(session, initiative_id)
    covered = initiative_coverage(session, initiative.id).ticket_ids()
    needle = f"%{search.strip()}%"
    query = select(Ticket).where(
        Ticket.work_item_type != WorkItemType.INITIATIVE,
        col(Ticket.title).ilike(needle) | col(Ticket.external_id).ilike(needle),
    )
    if covered:
        query = query.where(col(Ticket.id).not_in(covered))
    query = query.order_by(Ticket.priority, Ticket.created_at)
    # Open work first, then resolved to fill — each half in priority order.
    picked = list(
        session.exec(
            query.where(col(Ticket.state).not_in(list(RESOLVED_STATES))).limit(CANDIDATE_LIMIT)
        ).all()
    )
    if len(picked) < CANDIDATE_LIMIT:
        picked += session.exec(
            query.where(col(Ticket.state).in_(list(RESOLVED_STATES))).limit(
                CANDIDATE_LIMIT - len(picked)
            )
        ).all()
    slugs = workspace_slugs(session, picked)
    homes = home_milestones(session, picked)
    return [milestone_view(t, slugs, member=False, home=homes.get(t.id)) for t in picked]
