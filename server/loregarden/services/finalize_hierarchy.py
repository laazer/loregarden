"""Finalize a proposed hierarchy into persisted tickets.

Extracted from ``api.tickets`` so that module stays under the organization
line cap — the route stays a thin HTTP adapter over this service.
"""

from __future__ import annotations

from loregarden.models.domain import (
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.hierarchy_service import validate_parent_assignment
from loregarden.services.proposal_validator import ProposalValidationError, ProposalValidator
from loregarden.services.ticket_ids import assign_external_id
from loregarden.services.ticket_workspace_binding import validate_workspace_binding
from sqlmodel import Session, select


def _flatten_hierarchy(items: list) -> list:
    """Every item in the proposal, parent before child."""
    flattened = []
    for item in items:
        flattened.append(item)
        if item.children:
            flattened.extend(_flatten_hierarchy(item.children))
    return flattened


def _reject_conflicting_refs(session: Session, *, workspace_id: str, hierarchy: list) -> None:
    """Refuse a proposal whose refs collide with each other or with the workspace.

    Existing ids are collected under both spellings: a proposal's refs become
    legacy ids, so a resubmission collides there rather than on ``external_id``.
    """
    refs: set[str] = set()
    for item in _flatten_hierarchy(hierarchy):
        ref = item.external_id.strip()
        if ref in refs:
            raise ValueError(f"Duplicate external_id in hierarchy: {ref}")
        refs.add(ref)

    taken = {
        spelling
        for ticket in session.exec(select(Ticket).where(Ticket.workspace_id == workspace_id)).all()
        for spelling in (ticket.external_id, ticket.legacy_external_id)
        if spelling
    }
    for ref in refs:
        if ref in taken:
            raise ValueError(f"external_id already exists in workspace: {ref}")


def _resolve_parent(
    session: Session,
    item,
    *,
    ws: Workspace,
    parent_id: str | None,
) -> str | None:
    """Resolve parent id for one finalize-hierarchy node; validates type links."""
    parent_type = None
    if parent_id:
        parent = session.get(Ticket, parent_id)
        if not parent:
            raise ValueError(f"Parent not found: {parent_id}")
        parent_type = parent.work_item_type
    elif item.parent_ticket_id:
        linked = session.get(Ticket, item.parent_ticket_id)
        if not linked:
            raise ValueError("Parent work item not found in workspace")
        # Null-workspace INITIATIVE parents may own a workspace-bound child.
        if linked.workspace_id is not None and linked.workspace_id != ws.id:
            raise ValueError("Parent work item not found in workspace")
        parent_id = linked.id
        parent_type = linked.work_item_type

    validate_parent_assignment(item.work_item_type, parent_type)
    return parent_id


def _build_ticket(
    session: Session,
    item,
    *,
    ws: Workspace,
    parent_id: str | None,
    title: str,
) -> Ticket:
    """Create one finalize ticket row (initiative = null workspace)."""
    ext_id = item.external_id.strip()
    if not ext_id:
        raise ValueError("external_id is required")

    is_initiative = item.work_item_type == WorkItemType.INITIATIVE
    workspace_id = None if is_initiative else ws.id
    validate_workspace_binding(item.work_item_type, workspace_id)

    new_ticket = Ticket(
        external_id="",
        workspace_id=workspace_id,
        title=title,
        description=item.description.strip() if item.description else "",
        state=TicketState.BACKLOG,
        priority=item.priority,
        work_item_type=item.work_item_type,
        parent_ticket_id=parent_id,
        acceptance_criteria_json=serialize_criteria(item.acceptance_criteria),
        last_updated_by="system",
    )
    # Initiatives skip assign_external_id (null workspace; 733 owns scheme).
    if is_initiative:
        new_ticket.external_id = ext_id
        new_ticket.legacy_external_id = ext_id
    else:
        assign_external_id(session, new_ticket, ws, supplied_id=ext_id)
    session.add(new_ticket)
    session.flush()
    return new_ticket


def _create_item(
    session: Session,
    item,
    *,
    ws: Workspace,
    parent_id: str | None,
    created_ids: list[str],
    id_mapping: dict[str, str],
) -> None:
    """Create one hierarchy node and recurse into children."""
    title = item.title.strip()
    if not title:
        raise ValueError("Title is required")
    if item.priority < 1 or item.priority > 3:
        raise ValueError(f"Invalid priority {item.priority}: must be in [1, 3]")

    parent_id = _resolve_parent(session, item, ws=ws, parent_id=parent_id)
    new_ticket = _build_ticket(session, item, ws=ws, parent_id=parent_id, title=title)
    ticket_id = new_ticket.id
    created_ids.append(ticket_id)
    id_mapping[item.external_id.strip()] = ticket_id
    for child_item in item.children:
        _create_item(
            session,
            child_item,
            ws=ws,
            parent_id=ticket_id,
            created_ids=created_ids,
            id_mapping=id_mapping,
        )


def finalize_hierarchy(
    session: Session,
    *,
    workspace_slug: str,
    hierarchy: list,
) -> list[str]:
    """Persist a validated hierarchy. Returns created ticket ids in creation order.

    Raises ``ValueError`` / ``ProposalValidationError`` on bad input — the HTTP
    adapter maps those to 400.
    """
    ws = session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
    if not ws:
        raise ValueError(f"Workspace not found: {workspace_slug}")

    if not hierarchy:
        return []

    validated_hierarchy = ProposalValidator.validate_all(hierarchy)
    _reject_conflicting_refs(session, workspace_id=ws.id, hierarchy=validated_hierarchy)

    created_ids: list[str] = []
    id_mapping: dict[str, str] = {}
    for item in validated_hierarchy:
        _create_item(
            session,
            item,
            ws=ws,
            parent_id=None,
            created_ids=created_ids,
            id_mapping=id_mapping,
        )
    session.commit()
    return created_ids


__all__ = ["ProposalValidationError", "finalize_hierarchy"]
