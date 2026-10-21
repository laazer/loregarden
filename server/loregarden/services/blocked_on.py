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

import json
from collections.abc import Iterable

from loregarden.models.domain import (
    BlockedOn,
    BlockKind,
    DomainEvent,
    EventType,
    OrchestratorDecision,
    Ticket,
    TicketDependency,
    TicketState,
    WaitingOnRef,
    WaitingReason,
    WorkItemType,
    Workspace,
)
from loregarden.services.initiative_coverage import initiative_roots
from loregarden.services.ticket_state_service import RESOLVED_STATES
from sqlalchemy import ColumnElement, and_, exists, false, func, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

_AGENT_KINDS = (BlockKind.HARNESS, BlockKind.WORK)
_PERSON_KINDS = (BlockKind.DECISION, BlockKind.HUMAN_ACTION)

#: SQLite's default parameter limit is 999.
_ID_CHUNK = 400


#: States in which an unmet prerequisite is what the ticket waits for. In
#: progress counts: every dispatch is held until they land
#: (`services.dependency_hold`), so a ticket mid-workflow waits as surely as
#: one in the backlog.
_HELD_STATES = (TicketState.BACKLOG, TicketState.IN_PROGRESS)


def blocked_on(ticket: Ticket, *, has_unmet_prerequisite: bool) -> BlockedOn | None:
    """One ticket's answer. An unclassified block reads as a person's: someone should look.

    A block an agent could clear (harness, work) on a ticket that also waits on
    prerequisites reads as waiting on tickets: a repair would be held at its
    dispatch, so "agent can clear" promised something that could not happen.
    """
    if ticket.state == TicketState.BLOCKED:
        if ticket.block_kind is BlockKind.TICKETS:
            return BlockedOn.TICKETS
        if ticket.block_kind in _AGENT_KINDS:
            return BlockedOn.TICKETS if has_unmet_prerequisite else BlockedOn.AGENT
        return BlockedOn.PERSON
    if ticket.state in _HELD_STATES and has_unmet_prerequisite:
        return BlockedOn.TICKETS
    return None


def _overridden_pairs(session: Session, ticket_ids: list[str]) -> set[tuple[str, str]]:
    """(ticket, prerequisite) pairs a person started the ticket past.

    Every override counts, whenever it was made: an edge re-linked after its
    override holds the next dispatch (`dependency_hold`), and that rare case is
    the dispatch's to judge, not the board's.
    """
    pairs: set[tuple[str, str]] = set()
    for start in range(0, len(ticket_ids), _ID_CHUNK):
        events = session.exec(
            select(DomainEvent.ticket_id, DomainEvent.payload_json).where(
                col(DomainEvent.ticket_id).in_(ticket_ids[start : start + _ID_CHUNK]),
                _is_override(),
            )
        ).all()
        for ticket_id, payload in events:
            for item in json.loads(payload or "{}").get("prerequisites", []):
                pairs.add((ticket_id or "", item.get("ticket_id", "")))
    return pairs


def _is_override() -> ColumnElement[bool]:
    return and_(
        col(DomainEvent.type) == EventType.ORCHESTRATOR_DECISION,
        func.json_extract(col(DomainEvent.payload_json), "$.decision")
        == OrchestratorDecision.PREREQUISITES_OVERRIDDEN.value,
    )


def unmet_prerequisites(session: Session, ticket_ids: Iterable[str]) -> dict[str, list[Ticket]]:
    """Each ticket's unfinished prerequisites, by ticket id; absent when it has none.

    One a person started the ticket past no longer holds it, so it is left out.
    """
    ids = list(dict.fromkeys(ticket_ids))
    overridden = _overridden_pairs(session, ids)
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
            if (ticket_id, prerequisite.id) not in overridden:
                unmet.setdefault(ticket_id, []).append(prerequisite)
    return unmet


def blocked_on_map(session: Session, tickets: list[Ticket]) -> dict[str, BlockedOn]:
    """`blocked_on` for a page of tickets, in one query per chunk; absent when not stuck."""
    unmet = unmet_prerequisites(
        session,
        (t.id for t in tickets if t.state in (*_HELD_STATES, TicketState.BLOCKED)),
    )
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
    agent_block = and_(blocked, col(Ticket.block_kind).in_(_AGENT_KINDS))
    if BlockedOn.AGENT in wanted:
        clauses.append(and_(agent_block, ~_unmet_prerequisite_exists()))
    if BlockedOn.TICKETS in wanted:
        clauses.append(
            or_(
                and_(blocked, col(Ticket.block_kind) == BlockKind.TICKETS),
                and_(agent_block, _unmet_prerequisite_exists()),
                and_(col(Ticket.state).in_(_HELD_STATES), _unmet_prerequisite_exists()),
            )
        )
    return or_(*clauses) if clauses else false()


def _unmet_prerequisite_exists() -> ColumnElement[bool]:
    """EXISTS an unfinished prerequisite of the outer `Ticket` row that no
    override names — `unmet_prerequisites` in SQL."""
    prerequisite = aliased(Ticket)
    # Correlated by hand: two levels down, auto-correlation adds `tickets` to
    # this query's FROM rather than reaching the outermost row.
    overridden = exists(
        select(DomainEvent.id)
        .where(
            col(DomainEvent.ticket_id) == col(Ticket.id),
            _is_override(),
            col(DomainEvent.payload_json).contains(prerequisite.id),
        )
        .correlate(Ticket, prerequisite)
    )
    return exists(
        select(TicketDependency.id)
        .join(prerequisite, col(prerequisite.id) == col(TicketDependency.depends_on_ticket_id))
        .where(
            col(TicketDependency.ticket_id) == col(Ticket.id),
            col(prerequisite.state).not_in(RESOLVED_STATES),
            ~overridden,
        )
    )


def _workspace_slugs(session: Session) -> dict[str, str]:
    """Every workspace's slug by id: a handful of rows, read once per page."""
    return dict(session.exec(select(Workspace.id, Workspace.slug)).all())


def _ref(
    ticket: Ticket,
    reason: WaitingReason,
    slugs: dict[str, str],
    *,
    has_unmet_prerequisite: bool = False,
) -> WaitingOnRef:
    return WaitingOnRef(
        id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        state=ticket.state,
        reason=reason,
        blocked_on=blocked_on(ticket, has_unmet_prerequisite=has_unmet_prerequisite),
        workspace_slug=slugs.get(ticket.workspace_id or "", ""),
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
    slugs = _workspace_slugs(session) if unmet or tickets else {}
    waiting: dict[str, list[WaitingOnRef]] = {}
    for ticket in tickets:
        refs: list[WaitingOnRef] = []
        if ticket.state == TicketState.BLOCKED and ticket.block_kind is BlockKind.TICKETS:
            refs += [
                _ref(child, WaitingReason.BLOCKED_CHILD, slugs)
                for child in _blocking_descendants(session, ticket)
            ]
        refs += [
            _ref(prereq, WaitingReason.PREREQUISITE, slugs) for prereq in unmet.get(ticket.id, [])
        ]
        if refs:
            waiting[ticket.id] = refs
    return waiting


def prerequisite_refs_map(session: Session, tickets: list[Ticket]) -> dict[str, list[WaitingOnRef]]:
    """Each open ticket's unfinished prerequisites, as refs; absent when none.

    The tree's half of `waiting_on_map`: a tree already shows the blocked
    descendants under a parent, so it needs only the edges it cannot draw.
    """
    unmet = unmet_prerequisites(session, (t.id for t in tickets if t.state not in RESOLVED_STATES))
    if not unmet:
        return {}
    slugs = _workspace_slugs(session)
    return {
        ticket_id: [_ref(prereq, WaitingReason.PREREQUISITE, slugs) for prereq in prereqs]
        for ticket_id, prereqs in unmet.items()
    }


def root_blockers_map(session: Session, tickets: list[Ticket]) -> dict[str, list[WaitingOnRef]]:
    """For each ticket waiting on prerequisites, the tickets at the bottom of
    those chains: unfinished, and waiting on no unfinished prerequisite of their
    own. They are what has to move first. Absent when the ticket waits on none.

    A ticket that waits on 1314 which waits on 1237 is not unblocked by opening
    1314; the operator wants 1237, and whether it needs them. Live chains run
    fifteen deep (2026-10-20), so this walks the open-prerequisite graph one
    level per query, every page ticket at once, and stops at a node already seen
    (the graph is acyclic on insert, but a walk should not depend on it).
    """
    open_ids = [t.id for t in tickets if t.state not in RESOLVED_STATES]
    edges: dict[str, list[Ticket]] = {}
    frontier = open_ids
    while frontier:
        found = unmet_prerequisites(session, frontier)
        edges.update(found)
        # Every node walked is recorded, with or without prerequisites.
        for node in frontier:
            edges.setdefault(node, [])
        frontier = list(
            {prereq.id for prereqs in found.values() for prereq in prereqs} - edges.keys()
        )
    slugs = _workspace_slugs(session) if edges else {}
    roots: dict[str, list[WaitingOnRef]] = {}
    for ticket_id in open_ids:
        if not edges.get(ticket_id):
            continue
        found_roots: dict[str, Ticket] = {}
        seen: set[str] = {ticket_id}
        stack = list(edges[ticket_id])
        while stack:
            node = stack.pop()
            if node.id in seen:
                continue
            seen.add(node.id)
            below = edges.get(node.id, [])
            if below:
                stack.extend(below)
            else:
                found_roots[node.id] = node
        roots[ticket_id] = [
            _ref(root, WaitingReason.PREREQUISITE, slugs)
            for root in sorted(found_roots.values(), key=lambda t: (t.priority, t.created_at))
        ]
    return roots
