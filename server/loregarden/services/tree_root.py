"""The root of a ticket's landing tree, apart from the branches that tree lands on.

Split out of `target_branch` so the readers of delivery (`base_delivery`) can
name a tree's root without importing the module that moves its branch: that
module now moves branches through `integration_branch_moves`, whose landing
guard reads `base_delivery`, and the import would close a cycle.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, WorkItemType
from loregarden.services.hierarchy_service import validate_parent_child
from sqlmodel import Session


class TargetBranchError(RuntimeError):
    """The target branch could not be resolved or created."""


def subtree_root(session: Session, ticket: Ticket) -> Ticket:
    """The topmost ancestor of ``ticket`` below any initiative — ``ticket``
    itself when it has none.

    The one definition of a tree's root: landing, publishing and dependency
    readiness all reach it through here. A dangling ``parent_ticket_id`` (the
    parent row is gone) ends the walk at the last ticket that exists: it is the
    effective root of what remains.

    Raises :class:`TargetBranchError` for an initiative, which roots no tree,
    and for a ticket under one that the hierarchy does not allow there.
    """
    refuse_initiative(ticket)
    current = ticket
    seen = {ticket.id}
    while current.parent_ticket_id:
        parent = session.get(Ticket, current.parent_ticket_id)
        if parent is None or parent.id in seen:
            break
        if parent.work_item_type is WorkItemType.INITIATIVE:
            _require_initiative_child(parent, current)
            break
        seen.add(parent.id)
        current = parent
    return current


def refuse_initiative(ticket: Ticket) -> None:
    if ticket.work_item_type is WorkItemType.INITIATIVE:
        raise TargetBranchError(
            f"Initiative {ticket.external_id or ticket.id[:8]} is not a landing tree: "
            "its milestones each land on their own integration branch"
        )


def _require_initiative_child(initiative: Ticket, child: Ticket) -> None:
    try:
        validate_parent_child(WorkItemType.INITIATIVE, child.work_item_type)
    except ValueError as exc:
        raise TargetBranchError(
            f"{child.work_item_type.value} {child.external_id or child.id[:8]} sits directly "
            f"under initiative {initiative.external_id or initiative.id[:8]}, which the "
            f"hierarchy does not allow, so it has no tree to land in: {exc}"
        ) from exc
