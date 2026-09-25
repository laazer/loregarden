"""Workspace binding invariant for tickets.

``workspace_id`` is NULL if and only if ``work_item_type`` is INITIATIVE.
Pure — no Session / Workspace I/O. Session call sites resolve slugs and
enforce parent-workspace equality separately.
"""

from __future__ import annotations

from loregarden.models.domain import WorkItemType


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
