"""Workspace binding invariant for tickets.

``workspace_id`` is NULL if and only if ``work_item_type`` is INITIATIVE.
``validate_workspace_binding`` is pure — no Session / Workspace I/O; its call
sites resolve slugs and enforce parent-workspace equality separately. The read
side of the same rule lives here too, in ``workspace_for_ticket``, because 64
readers across the package re-derived it locally while this module claimed the
rule and owned only its write half.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, WorkItemType, Workspace
from sqlmodel import Session


def workspace_for_ticket(session: Session, ticket: Ticket) -> Workspace | None:
    """The ticket's workspace, or ``None`` when it has none — the one read of this rule.

    ``Ticket.workspace_id`` is NULL iff the work item is an initiative
    (``ck_tickets_workspace_binding``, ``models/domain/tables.py:139``), so passing it
    to ``Session.get`` unguarded asks for a row by a NULL primary key on any
    initiative-reachable path, and SQLAlchemy answers that with ``SAWarning: fully
    NULL primary key identity cannot load any object`` rather than a contract.

    ``None`` is the legitimate answer for an initiative, not an anomaly, so nothing
    is logged, warned or asserted here. The guard is spelled ``is not None`` and not
    truthiness: ``workspace_id == ""`` is falsy but a legal non-NULL primary key, and
    it stays on the call line because moving it to an early return takes the guard
    off the line every census of this rule greps for.
    """
    return session.get(Workspace, ticket.workspace_id) if ticket.workspace_id is not None else None


def validate_workspace_binding(work_item_type: WorkItemType, workspace_id: str | None) -> None:
    """Raise ``ValueError`` unless binding matches the initiative nullability rule.

    Uses identity (`is None`), not truthiness — an empty string is not None, so
    INITIATIVE + ``""`` is illegal and TASK + ``""`` is legal at this seam
    (FK enforcement is a later concern).
    """
    is_initiative = work_item_type == WorkItemType.INITIATIVE
    if is_initiative and workspace_id is not None:
        raise ValueError("Initiatives have no workspace binding — workspace_id must be null")
    if not is_initiative and workspace_id is None:
        raise ValueError(
            f"{work_item_type.value} requires a workspace binding — workspace_id must not be null"
        )
