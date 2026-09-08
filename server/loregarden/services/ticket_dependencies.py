"""Ticket dependency edges: directed, best-effort "waits for" links.

``ticket_id`` depends on ``depends_on_ticket_id`` and should run after it. The
edges are kept acyclic on insert.

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

from loregarden.models.domain import Ticket, TicketDependency, TicketState
from sqlmodel import Session, select


class DependencyCycleError(ValueError):
    """Adding an edge would create a cycle in the dependency graph."""


class TicketDependencyService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_dependency(
        self, ticket_id: str, depends_on_ticket_id: str, *, created_by: str = ""
    ) -> TicketDependency:
        """Link ``ticket_id`` to wait for ``depends_on_ticket_id``. Idempotent;
        rejects self-edges and any edge that would close a cycle."""
        if ticket_id == depends_on_ticket_id:
            raise ValueError("A ticket cannot depend on itself")
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
