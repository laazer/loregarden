"""Build ticket trees and validate parent-child relationships."""

from __future__ import annotations

from sqlmodel import Session, select

from loregarden.models.domain import (
    VALID_HIERARCHY,
    Ticket,
    TicketTreeNode,
    WorkItemType,
)


def child_count(session: Session, ticket_id: str) -> int:
    return len(
        session.exec(
            select(Ticket).where(Ticket.parent_ticket_id == ticket_id)
        ).all()
    )


def validate_parent_child(parent_type: WorkItemType, child_type: WorkItemType) -> None:
    allowed = VALID_HIERARCHY.get(parent_type, [])
    if child_type not in allowed:
        raise ValueError(
            f"{parent_type.value} cannot contain {child_type.value}; "
            f"allowed: {[t.value for t in allowed]}"
        )


def build_tree(
    session: Session,
    tickets: list[Ticket],
    *,
    stage_names: dict[str, str] | None = None,
) -> list[TicketTreeNode]:
    """Assemble a forest from a flat ticket list (roots = no parent)."""
    stage_names = stage_names or {}
    by_id = {t.id: t for t in tickets}
    children_map: dict[str | None, list[Ticket]] = {}
    for t in tickets:
        pid = t.parent_ticket_id
        if pid and pid not in by_id:
            pid = None
        children_map.setdefault(pid, []).append(t)

    def sort_key(t: Ticket) -> tuple:
        type_order = {
            WorkItemType.MILESTONE: 0,
            WorkItemType.FEATURE: 1,
            WorkItemType.CAPABILITY: 2,
            WorkItemType.TASK: 3,
            WorkItemType.BUG: 4,
        }
        return (type_order.get(t.work_item_type, 9), t.priority, t.external_id)

    def node_for(ticket: Ticket) -> TicketTreeNode:
        kids = sorted(children_map.get(ticket.id, []), key=sort_key)
        return TicketTreeNode(
            id=ticket.id,
            external_id=ticket.external_id,
            title=ticket.title,
            state=ticket.state,
            priority=ticket.priority,
            work_item_type=ticket.work_item_type,
            workflow_stage_name=stage_names.get(ticket.id, ""),
            child_count=len(kids),
            children=[node_for(k) for k in kids],
        )

    roots = sorted(children_map.get(None, []), key=sort_key)
    return [node_for(r) for r in roots]
