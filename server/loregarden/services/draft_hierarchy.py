"""Repair and validate Ticket Studio draft hierarchies before they are persisted.

Work items form a strict chain — milestone → feature → capability → task — with bugs
as leaves under any of the three. Scoping models and the ticket importer routinely skip
a layer: a task hung straight off a feature, or a batch of parentless tasks. Those
drafts used to be written to the session unchecked and only rejected later, on save or
commit, which left the session unusable. Close the mechanical gaps here instead, and
reject outright the drafts that no insertion can rescue.
"""

from __future__ import annotations

from loregarden.models.domain import VALID_HIERARCHY, TicketStudioDraftItem, WorkItemType

_CHAIN: tuple[WorkItemType, ...] = (
    WorkItemType.MILESTONE,
    WorkItemType.FEATURE,
    WorkItemType.CAPABILITY,
    WorkItemType.TASK,
)
_CHAIN_RANK: dict[WorkItemType, int] = {item: rank for rank, item in enumerate(_CHAIN)}
_TASK_RANK = _CHAIN_RANK[WorkItemType.TASK]

_ROOT_KEY = "root"
_ROOT_RANK = -1

SYNTHESIZED_NOTE = (
    "Added automatically to keep the milestone → feature → capability → task hierarchy "
    "intact. Rename, split, or re-parent it before committing."
)


class DraftHierarchyError(ValueError):
    """A draft hierarchy that cannot be saved, listing every offending item."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


def _missing_ranks(parent_rank: int | None, child_type: WorkItemType) -> list[int] | None:
    """Chain ranks to insert between parent and child; None when no insertion helps."""
    if parent_rank is None:
        # Parent is a bug — a leaf that never takes children.
        return None
    if child_type == WorkItemType.BUG:
        if parent_rank == _ROOT_RANK:
            return [_CHAIN_RANK[WorkItemType.MILESTONE]]
        if parent_rank >= _TASK_RANK:
            return None
        return []
    child_rank = _CHAIN_RANK[child_type]
    if child_rank <= parent_rank:
        return None
    return list(range(parent_rank + 1, child_rank))


def topo_sort_draft_items(items: list[TicketStudioDraftItem]) -> list[TicketStudioDraftItem]:
    """Order a draft parents-first, so it can be read — and created — top down.

    Items caught in a parent_ref cycle have no valid position; they are emitted last,
    in their original order, rather than dropped. Reject such drafts before storing
    them: find_hierarchy_violations reports the cycle.
    """
    by_ref = {item.ref: item for item in items}
    ordered: list[TicketStudioDraftItem] = []
    placed: set[str] = set()

    for item in items:
        chain: list[TicketStudioDraftItem] = []
        walked: set[str] = set()
        cursor: TicketStudioDraftItem | None = item
        while cursor and cursor.ref not in placed and cursor.ref not in walked:
            chain.append(cursor)
            walked.add(cursor.ref)
            cursor = by_ref.get(cursor.parent_ref) if cursor.parent_ref else None
        if cursor and cursor.ref in walked:
            continue  # cycle — leave every member for the trailing pass
        for entry in reversed(chain):
            ordered.append(entry)
            placed.add(entry.ref)

    ordered.extend(item for item in items if item.ref not in placed)
    return ordered


def _cycle_refs(items: list[TicketStudioDraftItem]) -> list[str]:
    """Refs whose parent chain loops back on itself."""
    by_ref = {item.ref: item for item in items}
    cyclic: set[str] = set()
    settled: set[str] = set()

    for item in items:
        walked: list[str] = []
        seen: set[str] = set()
        cursor: TicketStudioDraftItem | None = item
        while cursor and cursor.ref not in settled and cursor.ref not in seen:
            walked.append(cursor.ref)
            seen.add(cursor.ref)
            cursor = by_ref.get(cursor.parent_ref) if cursor.parent_ref else None
        if cursor and cursor.ref in seen:
            cyclic.update(walked[walked.index(cursor.ref) :])
        settled.update(walked)

    return [item.ref for item in items if item.ref in cyclic]


def _unique_ref(base: str, used_refs: set[str]) -> str:
    ref = base
    suffix = 2
    while ref in used_refs:
        ref = f"{base}-{suffix}"
        suffix += 1
    used_refs.add(ref)
    return ref


def find_hierarchy_violations(
    items: list[TicketStudioDraftItem],
    *,
    parent_type: WorkItemType | None,
) -> list[str]:
    """Every reason this draft cannot be committed, not just the first one."""
    by_ref = {item.ref: item for item in items}
    violations: list[str] = [f"{ref}: parent_ref chain forms a cycle" for ref in _cycle_refs(items)]

    for item in items:
        if item.parent_ref and item.parent_ref not in by_ref:
            violations.append(f"{item.ref}: references unknown parent_ref '{item.parent_ref}'")
            continue

        if item.parent_ref:
            parent_item = by_ref[item.parent_ref]
            allowed = VALID_HIERARCHY.get(parent_item.work_item_type, [])
            if item.work_item_type not in allowed:
                violations.append(
                    f"{item.ref} ({item.work_item_type.value}) cannot be a child of "
                    f"{parent_item.ref} ({parent_item.work_item_type.value}); "
                    f"allowed there: {[t.value for t in allowed]}"
                )
        elif parent_type is not None:
            allowed = VALID_HIERARCHY.get(parent_type, [])
            if item.work_item_type not in allowed:
                violations.append(
                    f"{item.ref} ({item.work_item_type.value}) cannot be a child of the "
                    f"session's parent work item ({parent_type.value}); "
                    f"allowed there: {[t.value for t in allowed]}"
                )
        elif item.work_item_type not in {WorkItemType.MILESTONE, WorkItemType.FEATURE}:
            violations.append(
                f"{item.ref} ({item.work_item_type.value}) must be a milestone or feature "
                "to sit at the root when the session has no parent work item"
            )

    return violations


def repair_draft_hierarchy(
    items: list[TicketStudioDraftItem],
    *,
    parent_type: WorkItemType | None,
    root_title: str,
    root_description: str = "",
) -> list[TicketStudioDraftItem]:
    """Insert the layers a draft skipped, so it is legal before it is stored.

    Items are re-parented onto synthesized wrappers, which are shared between siblings:
    twelve tasks hung off one feature gain one capability between them, not twelve.
    The result is ordered parents-first. Raises DraftHierarchyError when a draft is
    broken in a way no insertion fixes — an unknown parent_ref, a parent_ref cycle, or
    a child that outranks its parent.
    """
    if not items:
        return items

    by_ref = {item.ref: item for item in items}
    broken = [
        f"{item.ref}: references unknown parent_ref '{item.parent_ref}'"
        for item in items
        if item.parent_ref and item.parent_ref not in by_ref
    ]
    broken.extend(f"{ref}: parent_ref chain forms a cycle" for ref in _cycle_refs(items))
    if broken:
        raise DraftHierarchyError(broken)

    used_refs = set(by_ref)
    wrappers: dict[tuple[str, int], TicketStudioDraftItem] = {}
    violations: list[str] = []

    for item in items:
        if item.parent_ref:
            parent_item = by_ref[item.parent_ref]
            parent_key = parent_item.ref
            parent_rank = _CHAIN_RANK.get(parent_item.work_item_type)
            parent_label = f"{parent_item.ref} ({parent_item.work_item_type.value})"
            parent_title = parent_item.title
        else:
            parent_key = _ROOT_KEY
            parent_rank = _CHAIN_RANK.get(parent_type) if parent_type else _ROOT_RANK
            parent_label = (
                f"the session's parent work item ({parent_type.value})"
                if parent_type
                else "the draft root"
            )
            parent_title = root_title

        missing = _missing_ranks(parent_rank, item.work_item_type)
        if missing is None:
            violations.append(
                f"{item.ref} ({item.work_item_type.value}) cannot be a child of {parent_label}"
            )
            continue

        chain_parent_ref = item.parent_ref
        for rank in missing:
            key = (parent_key, rank)
            wrapper = wrappers.get(key)
            if wrapper is None:
                is_root_milestone = (
                    parent_key == _ROOT_KEY and _CHAIN[rank] == WorkItemType.MILESTONE
                )
                wrapper = TicketStudioDraftItem(
                    ref=_unique_ref(f"{parent_key}-{_CHAIN[rank].value}", used_refs),
                    work_item_type=_CHAIN[rank],
                    parent_ref=chain_parent_ref,
                    title=parent_title,
                    description=root_description if is_root_milestone else SYNTHESIZED_NOTE,
                    priority=item.priority,
                    selected=True,
                )
                wrappers[key] = wrapper
            chain_parent_ref = wrapper.ref
        item.parent_ref = chain_parent_ref

    if violations:
        raise DraftHierarchyError(violations)

    return topo_sort_draft_items([*wrappers.values(), *items])
