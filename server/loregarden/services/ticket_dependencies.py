"""Ticket dependency edges: directed, best-effort "waits for" links.

``ticket_id`` depends on ``depends_on_ticket_id`` and should run after it. The
edges are kept acyclic on insert, and a ticket cannot wait on an ancestor:
the parent is done only when its children are, so that edge deadlocks both.

Two consumers, and they answer different questions. ``order_children_for_subtree``
in subtree_auto_run asks *what order should these siblings run in*, and ignores
edges pointing outside the sibling set. ``unmet_prerequisites`` here asks *may
this ticket start at all*, and ignores nothing — which is what makes an edge to
another workspace mean something. Before it existed, every cross-workspace edge
was recorded, rendered in the UI as a prerequisite, and had no effect on
anything (676).

Enforcement is deliberately asymmetric, and the asymmetry is the point:

- The orchestrator will not *choose* a ticket whose prerequisites are unmet. It
  holds it and reports why, the way it already holds a parked child.
- An operator starting a named ticket is not blocked. That escape is why this
  module said edges "do not hard-block a standalone run" from the beginning, and
  removing it would let one stale edge wedge a ticket with no way out.

So the system stops picking up work that cannot succeed yet, and a person can
still override it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from loregarden.models.domain import Ticket, TicketDependency, TicketState
from loregarden.services.ticket_discovery import looks_like_ticket_uuid
from loregarden.services.ticket_ids import TicketNotFound, resolve_one
from sqlmodel import Session, select

#: Raised when an edge would wait on a parent (or further ancestor). Cycle
#: detection only walks ``ticket_dependencies``; the parent→child wait is
#: hierarchy, so this deadlock was invisible to it (lg-milestone-that-1604).
ANCESTOR_WAIT_REFUSED = (
    "A ticket cannot depend on an ancestor; a parent is done only when its children are"
)
REPARENT_ONTO_PREREQUISITE_REFUSED = (
    "Cannot reparent under a ticket this subtree already depends on"
)


class DependencyCycleError(ValueError):
    """Adding an edge would create a cycle in the dependency graph."""


def resolve_prerequisites(session: Session, refs: Iterable[str]) -> list[Ticket]:
    """The tickets ``refs`` name (UUIDs or external ids), in order, deduplicated.

    Raises `TicketNotFound` naming the first that resolves to nothing, so a
    caller can refuse before writing anything.
    """
    found: dict[str, Ticket] = {}
    for ref in refs:
        value = ref.strip()
        if not value:
            continue
        ticket = session.get(Ticket, value)
        if ticket is None:
            if looks_like_ticket_uuid(value):
                raise TicketNotFound(value)
            ticket = resolve_one(session, value)
        found.setdefault(ticket.id, ticket)
    return list(found.values())


def ancestor_ids(session: Session, ticket_id: str) -> set[str]:
    """Every parent walking up from ``ticket_id``, bounded by a seen-set so a
    stored parent cycle cannot hang the writer that would report it."""
    found: set[str] = set()
    current = session.get(Ticket, ticket_id)
    while current is not None and current.parent_ticket_id:
        if current.parent_ticket_id in found:
            break
        found.add(current.parent_ticket_id)
        current = session.get(Ticket, current.parent_ticket_id)
    return found


def refuse_wait_on_ancestor(session: Session, ticket_id: str, depends_on_ticket_id: str) -> None:
    """Raise if ``depends_on_ticket_id`` sits above ``ticket_id`` in the tree."""
    if depends_on_ticket_id in ancestor_ids(session, ticket_id):
        raise ValueError(ANCESTOR_WAIT_REFUSED)


def refuse_reparent_onto_a_prerequisite(
    session: Session, ticket_id: str, new_parent_id: str
) -> None:
    """Raise if attaching ``ticket_id`` under ``new_parent_id`` would make any
    ticket in that subtree wait on a new ancestor.

    The other half of the conjunction ``add_dependency`` refuses: a parent link
    completing an ancestor-wait. ``reparent_ticket`` and the workspace-move
    parent rewrite both call this before writing ``parent_ticket_id``.
    """
    new_ancestors = {new_parent_id} | ancestor_ids(session, new_parent_id)
    subtree = _subtree_ids(session, ticket_id)
    rows = session.exec(
        select(TicketDependency).where(TicketDependency.ticket_id.in_(subtree))
    ).all()
    if any(row.depends_on_ticket_id in new_ancestors for row in rows):
        raise ValueError(REPARENT_ONTO_PREREQUISITE_REFUSED)


def _subtree_ids(session: Session, root_id: str) -> set[str]:
    found = {root_id}
    frontier = [root_id]
    while frontier:
        children = session.exec(
            select(Ticket.id).where(Ticket.parent_ticket_id.in_(frontier))
        ).all()
        frontier = [child_id for child_id in children if child_id not in found]
        found.update(frontier)
    return found


@dataclass(frozen=True)
class AncestorWait:
    """One ``ticket_dependencies`` row whose far end is an ancestor."""

    edge: TicketDependency
    dependent: Ticket
    ancestor: Ticket


def ancestor_waits(session: Session) -> list[AncestorWait]:
    """Edges from a ticket to a parent or further ancestor.

    Loads the parent map once and walks it in Python: the tree is hundreds of
    rows, and a recursive CTE would be the same walk with worse tests.
    """
    parent_of = dict(session.exec(select(Ticket.id, Ticket.parent_ticket_id)).all())
    tickets = {row.id: row for row in session.exec(select(Ticket)).all()}
    found: list[AncestorWait] = []
    for edge in session.exec(select(TicketDependency)).all():
        seen: set[str] = set()
        cursor = parent_of.get(edge.ticket_id) or None
        while cursor:
            if cursor == edge.depends_on_ticket_id:
                dependent = tickets.get(edge.ticket_id)
                ancestor = tickets.get(edge.depends_on_ticket_id)
                if dependent is not None and ancestor is not None:
                    found.append(AncestorWait(edge=edge, dependent=dependent, ancestor=ancestor))
                break
            if cursor in seen:
                break
            seen.add(cursor)
            cursor = parent_of.get(cursor) or None
    return found


def drop_ancestor_waits(session: Session, waits: Sequence[AncestorWait]) -> int:
    """Delete the given ancestor-wait edges. Re-reads each row; the world may
    have moved between detect and repair. Returns how many it deleted."""
    deleted = 0
    for wait in waits:
        edge = session.get(TicketDependency, wait.edge.id)
        if edge is None:
            continue
        session.delete(edge)
        deleted += 1
    if deleted:
        session.commit()
    return deleted


class TicketDependencyService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_dependency(
        self, ticket_id: str, depends_on_ticket_id: str, *, created_by: str = ""
    ) -> TicketDependency:
        """Link ``ticket_id`` to wait for ``depends_on_ticket_id``. Idempotent;
        rejects self-edges, ancestor-waits, and any edge that would close a cycle."""
        if ticket_id == depends_on_ticket_id:
            raise ValueError("A ticket cannot depend on itself")
        refuse_wait_on_ancestor(self.session, ticket_id, depends_on_ticket_id)
        existing = self.session.exec(
            select(TicketDependency).where(
                TicketDependency.ticket_id == ticket_id,
                TicketDependency.depends_on_ticket_id == depends_on_ticket_id,
            )
        ).first()
        if existing:
            return existing
        # A cycle would form iff the prospective prerequisite already (transitively)
        # depends on the dependent.
        if self._reaches(depends_on_ticket_id, ticket_id):
            raise DependencyCycleError(
                f"{depends_on_ticket_id} already depends on {ticket_id}; edge would cycle"
            )
        edge = TicketDependency(
            ticket_id=ticket_id,
            depends_on_ticket_id=depends_on_ticket_id,
            created_by=created_by,
        )
        self.session.add(edge)
        self.session.commit()
        self.session.refresh(edge)
        return edge

    def remove_dependency(self, ticket_id: str, depends_on_ticket_id: str) -> bool:
        edge = self.session.exec(
            select(TicketDependency).where(
                TicketDependency.ticket_id == ticket_id,
                TicketDependency.depends_on_ticket_id == depends_on_ticket_id,
            )
        ).first()
        if not edge:
            return False
        self.session.delete(edge)
        self.session.commit()
        return True

    def prerequisites(self, ticket_id: str) -> list[str]:
        return list(
            self.session.exec(
                select(TicketDependency.depends_on_ticket_id).where(
                    TicketDependency.ticket_id == ticket_id
                )
            ).all()
        )

    #: A prerequisite stops blocking when it reaches one of these. ``WONT_DO``
    #: counts: work that will never be done cannot be waited for, and treating it
    #: as unmet is how a dependency graph deadlocks on a decision already taken.
    SATISFIED_STATES = (TicketState.DONE, TicketState.WONT_DO)

    def unmet_prerequisites(self, ticket_id: str) -> list[Ticket]:
        """Prerequisite tickets that have not reached a satisfied state.

        Returns the ticket rows, not ids, because the caller has to be able to
        *name* them — and for a cross-workspace edge the name alone is not
        enough. The board you are looking at does not show the other workspace,
        so "waiting on lor-extract-lore-35" is only actionable with the
        workspace beside it.

        No workspace filter, anywhere in this path. An edge is two ticket ids;
        that they belong to different workspaces is not a special case to
        support, it is the absence of a restriction nobody had reason to add.
        """
        prerequisite_ids = self.prerequisites(ticket_id)
        if not prerequisite_ids:
            return []
        rows = self.session.exec(select(Ticket).where(Ticket.id.in_(prerequisite_ids))).all()
        return [row for row in rows if row.state not in self.SATISFIED_STATES]

    def dependents(self, ticket_id: str) -> list[str]:
        return list(
            self.session.exec(
                select(TicketDependency.ticket_id).where(
                    TicketDependency.depends_on_ticket_id == ticket_id
                )
            ).all()
        )

    def prerequisites_map(self, ticket_ids: list[str]) -> dict[str, set[str]]:
        """Prerequisite ids for each id in ``ticket_ids`` (edges to tickets outside
        the set are included; callers restrict to the set if they only order it)."""
        ids = set(ticket_ids)
        result: dict[str, set[str]] = {tid: set() for tid in ids}
        if not ids:
            return result
        rows = self.session.exec(
            select(TicketDependency).where(TicketDependency.ticket_id.in_(ids))
        ).all()
        for row in rows:
            result[row.ticket_id].add(row.depends_on_ticket_id)
        return result

    def _reaches(self, start: str, target: str) -> bool:
        """Whether ``start`` reaches ``target`` by following depends-on edges."""
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(
                self.session.exec(
                    select(TicketDependency.depends_on_ticket_id).where(
                        TicketDependency.ticket_id == current
                    )
                ).all()
            )
        return False
