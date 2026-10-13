"""What a stuck ticket waits for: a person, other tickets, or an agent.

`TicketState.BLOCKED` plus its `BlockKind` is the stored half. A backlog ticket
held by an unmet prerequisite is never `blocked` (the dispatchers step over it
and take it once the prerequisite finishes), so that half is derived here from
the dependency edges. Both answer the operator's one question: is this mine to
unblock, or is it waiting on something else?

Prerequisites are judged by state, as `TicketDependencyService.unmet_prerequisites`
judges them: a ticket list cannot pay for a git probe per row. A prerequisite
that is done but not landed therefore reads as met here, while
`dependency_readiness` still holds its dependent.
"""

from __future__ import annotations

from collections.abc import Iterable

from loregarden.models.domain import (
    BlockedOn,
    BlockKind,
    Ticket,
    TicketDependency,
    TicketState,
    WaitingOnRef,
    WaitingReason,
    WorkItemType,
)
from loregarden.services.initiative_coverage import initiative_roots
from loregarden.services.ticket_state_service import RESOLVED_STATES
from sqlalchemy import ColumnElement, and_, exists, false, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

_AGENT_KINDS = (BlockKind.HARNESS, BlockKind.WORK)
_PERSON_KINDS = (BlockKind.DECISION, BlockKind.HUMAN_ACTION)

#: SQLite's default parameter limit is 999.
_ID_CHUNK = 400


def blocked_on(ticket: Ticket, *, has_unmet_prerequisite: bool) -> BlockedOn | None:
    """One ticket's answer. An unclassified block reads as a person's: someone should look."""
    if ticket.state == TicketState.BLOCKED:
        if ticket.block_kind is BlockKind.TICKETS:
            return BlockedOn.TICKETS
        if ticket.block_kind in _AGENT_KINDS:
            return BlockedOn.AGENT
        return BlockedOn.PERSON
    if ticket.state == TicketState.BACKLOG and has_unmet_prerequisite:
        return BlockedOn.TICKETS
    return None


def unmet_prerequisites(session: Session, ticket_ids: Iterable[str]) -> dict[str, list[Ticket]]:
    """Each ticket's unfinished prerequisites, by ticket id; absent when it has none."""
    ids = list(dict.fromkeys(ticket_ids))
    unmet: dict[str, list[Ticket]] = {}
    for start in range(0, len(ids), _ID_CHUNK):
        rows = session.exec(
            select(TicketDependency.ticket_id, Ticket)
            .join(Ticket, col(Ticket.id) == col(TicketDependency.depends_on_ticket_id))
            .where(
                col(TicketDependency.ticket_id).in_(ids[start : start + _ID_CHUNK]),
                col(Ticket.state).not_in(RESOLVED_STATES),
            )
            .order_by(col(Ticket.priority), col(Ticket.created_at))
        ).all()
        for ticket_id, prerequisite in rows:
            unmet.setdefault(ticket_id, []).append(prerequisite)
    return unmet


def blocked_on_map(session: Session, tickets: list[Ticket]) -> dict[str, BlockedOn]:
    """`blocked_on` for a page of tickets, in one query per chunk; absent when not stuck."""
    unmet = unmet_prerequisites(session, (t.id for t in tickets if t.state == TicketState.BACKLOG))
    answers: dict[str, BlockedOn] = {}
    for ticket in tickets:
        answer = blocked_on(ticket, has_unmet_prerequisite=ticket.id in unmet)
        if answer is not None:
            answers[ticket.id] = answer
    return answers


def blocked_on_clause(wanted: list[BlockedOn]) -> ColumnElement[bool]:
    """The SQL twin of `blocked_on`, for filtering a ticket query by its answer."""
    blocked = col(Ticket.state) == TicketState.BLOCKED
    clauses: list[ColumnElement[bool]] = []
    if BlockedOn.PERSON in wanted:
        clauses.append(
            and_(
                blocked,
                or_(col(Ticket.block_kind).is_(None), col(Ticket.block_kind).in_(_PERSON_KINDS)),
            )
        )
    if BlockedOn.AGENT in wanted:
        clauses.append(and_(blocked, col(Ticket.block_kind).in_(_AGENT_KINDS)))
    if BlockedOn.TICKETS in wanted:
        clauses.append(
            or_(
                and_(blocked, col(Ticket.block_kind) == BlockKind.TICKETS),
                and_(col(Ticket.state) == TicketState.BACKLOG, _unmet_prerequisite_exists()),
            )
        )
    return or_(*clauses) if clauses else false()


def _unmet_prerequisite_exists() -> ColumnElement[bool]:
    """EXISTS an unfinished prerequisite of the outer `Ticket` row."""
    prerequisite = aliased(Ticket)
    return exists(
        select(TicketDependency.id)
        .join(prerequisite, col(prerequisite.id) == col(TicketDependency.depends_on_ticket_id))
        .where(
            col(TicketDependency.ticket_id) == col(Ticket.id),
            col(prerequisite.state).not_in(RESOLVED_STATES),
        )
    )


def _ref(ticket: Ticket, reason: WaitingReason) -> WaitingOnRef:
    return WaitingOnRef(
        id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        state=ticket.state,
        reason=reason,
        blocked_on=blocked_on(ticket, has_unmet_prerequisite=False),
    )


def _blocked_children(session: Session, parents: list[Ticket]) -> list[Ticket]:
    """The blocked children of `parents`, an initiative's members included."""
    ids = [p.id for p in parents]
    children = list(
        session.exec(
            select(Ticket).where(
                col(Ticket.parent_ticket_id).in_(ids), Ticket.state == TicketState.BLOCKED
            )
        ).all()
    )
    initiatives = [p.id for p in parents if p.work_item_type == WorkItemType.INITIATIVE]
    for roots in initiative_roots(session, initiatives).values():
        children += [t for t in roots.roots if t.state == TicketState.BLOCKED]
    return children


def _blocking_descendants(session: Session, parent: Ticket) -> list[Ticket]:
    """The blocked tickets under a rolled-up parent that hold it: the bottom of each
    blocked chain, not the intermediate parents that only repeat the block."""
    found: dict[str, Ticket] = {}
    seen = {parent.id}
    frontier = [parent]
    while frontier:
        next_frontier: list[Ticket] = []
        for child in _blocked_children(session, frontier):
            if child.id in seen:
                continue
            seen.add(child.id)
            if child.block_kind is BlockKind.TICKETS:
                next_frontier.append(child)
            else:
                found[child.id] = child
        frontier = next_frontier
    return list(found.values())


def waiting_on_map(session: Session, tickets: list[Ticket]) -> dict[str, list[WaitingOnRef]]:
    """The tickets each one waits on; absent when it waits on none.

    A rolled-up parent names its blocking descendants; any open ticket names
    its unfinished prerequisites. Prerequisites come in one query; descendants
    cost a query per level, for the few parents blocked that way.
    """
    unmet = unmet_prerequisites(session, (t.id for t in tickets if t.state not in RESOLVED_STATES))
    waiting: dict[str, list[WaitingOnRef]] = {}
    for ticket in tickets:
        refs: list[WaitingOnRef] = []
        if ticket.state == TicketState.BLOCKED and ticket.block_kind is BlockKind.TICKETS:
            refs += [
                _ref(child, WaitingReason.BLOCKED_CHILD)
                for child in _blocking_descendants(session, ticket)
            ]
        refs += [_ref(prereq, WaitingReason.PREREQUISITE) for prereq in unmet.get(ticket.id, [])]
        if refs:
            waiting[ticket.id] = refs
    return waiting
