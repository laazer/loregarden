"""Ticket dependency edges: directed, best-effort "waits for" links.

``ticket_id`` depends on ``depends_on_ticket_id`` and should run after it. The
edges are kept acyclic on insert. They steer subtree run order (see
``order_children_for_subtree`` in subtree_auto_run) — they do not hard-block a
standalone run, so an operator can always force a ticket to run out of order.
"""

from __future__ import annotations

from loregarden.models.domain import TicketDependency
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
