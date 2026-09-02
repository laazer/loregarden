"""Build ticket trees and validate parent-child relationships."""

from __future__ import annotations

from loregarden.models.domain import (
    VALID_HIERARCHY,
    Ticket,
    TicketTreeNode,
    WorkItemType,
    Workspace,
)
from sqlmodel import Session, select


def child_count(session: Session, ticket_id: str) -> int:
    return len(session.exec(select(Ticket).where(Ticket.parent_ticket_id == ticket_id)).all())


def collect_ticket_scope_ids(session: Session, ticket_id: str) -> list[str]:
    """Ticket id plus all descendant work items (for triage/inbox roll-up)."""
    scope = [ticket_id]
    queue = [ticket_id]
    while queue:
        parent_id = queue.pop()
        children = session.exec(select(Ticket.id).where(Ticket.parent_ticket_id == parent_id)).all()
        for child_id in children:
            if child_id not in scope:
                scope.append(child_id)
                queue.append(child_id)
    return scope


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
    workspace_slugs: dict[str, str] = {}
    for ticket in tickets:
        if ticket.workspace_id not in workspace_slugs:
            ws = session.get(Workspace, ticket.workspace_id)
            workspace_slugs[ticket.workspace_id] = ws.slug if ws else ""
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
            workspace_slug=workspace_slugs.get(ticket.workspace_id, ""),
            workflow_stage_name=stage_names.get(ticket.id, ""),
            workflow_stage_status=ticket.workflow_stage_status,
            child_count=len(kids),
            children=[node_for(k) for k in kids],
        )

    roots = sorted(children_map.get(None, []), key=sort_key)
    return [node_for(r) for r in roots]


def _is_descendant(session: Session, *, candidate: Ticket, ancestor: Ticket) -> bool:
    """Whether `candidate` sits somewhere under `ancestor`.

    Walks parent links upward, which is bounded by the depth of the tree rather
    than its width. The `seen` set is not defensive: it is what stops an
    already-corrupt cycle in the stored data from hanging the request that
    would otherwise report it.
    """
    seen: set[str] = set()
    current: Ticket | None = candidate
    while current is not None and current.parent_ticket_id:
        if current.parent_ticket_id in seen:
            return False
        seen.add(current.parent_ticket_id)
        if current.parent_ticket_id == ancestor.id:
            return True
        current = session.get(Ticket, current.parent_ticket_id)
    return False


def reparent_ticket(session: Session, ticket: Ticket, parent_ticket_id: str | None) -> Ticket:
    """Move a work item under a different parent.

    Exists so a human-gated ticket can be lifted out of a critical path
    directly, rather than by the four-step manual workaround parking replaces:
    split the work, amend the criteria, clear the block, mark it done
    (lg-workflow-integrity-449).

    Lives here rather than on `TicketService` for two reasons. It is the same
    concern `validate_parent_child` already owns, and `ticket_service` imports
    `orchestration` at module level — so the REST patch path could only reach a
    method on that service through a function-local import, which this repo
    treats as a cycle to fix rather than a cycle to dodge.

    The rules are creation's rules: a milestone takes no parent, everything else
    requires one, and `validate_parent_child` decides which pairs are legal. The
    one rule creation does not need is the cycle check — a ticket being created
    has no descendants, so only a move can put a ticket underneath itself.
    """
    if ticket.work_item_type == WorkItemType.MILESTONE:
        if parent_ticket_id:
            raise ValueError("Milestones cannot have a parent")
        ticket.parent_ticket_id = None
    else:
        if not parent_ticket_id:
            raise ValueError(f"{ticket.work_item_type.value} requires a parent work item")
        parent = session.get(Ticket, parent_ticket_id)
        if not parent or parent.workspace_id != ticket.workspace_id:
            raise ValueError("Parent work item not found in workspace")
        if parent.id == ticket.id:
            raise ValueError("A work item cannot be its own parent")
        if _is_descendant(session, candidate=parent, ancestor=ticket):
            raise ValueError("Cannot reparent a work item beneath one of its own descendants")
        validate_parent_child(parent.work_item_type, ticket.work_item_type)
        ticket.parent_ticket_id = parent.id

    ticket.revision += 1
    ticket.last_updated_by = "human"
    session.add(ticket)
    return ticket
