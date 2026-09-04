"""Alternative stage groups: a set of stages of which at least one must run.

Lifted out of `services.studio_routing` for the same reason as
`core.workflow_terminal`: `services.workflow_state` has to ask whether a group has
been emptied, and importing `studio_routing` from there closes a cycle
(`workflow_state` -> `studio_routing` -> `workflow_service` -> `workflow_state`).
These predicates are pure functions over stage definitions and a status map —
they need no session and no registry, so they belong below the services layer.
`studio_routing` re-exports them, so existing import sites are unchanged.
"""

from __future__ import annotations

from loregarden.models.domain import StageStatus, WorkflowStageDef


def group_members(stages: list[WorkflowStageDef], group: str) -> list[WorkflowStageDef]:
    """Every stage in the named alternative group, in template order.

    An empty group name is not a group — it is the default for a stage that
    stands alone — so it never has members.
    """
    if not group:
        return []
    return [s for s in sorted(stages, key=lambda s: s.order) if s.alternative_group == group]


def group_would_be_emptied(
    stages: list[WorkflowStageDef],
    stage_map: dict[str, StageStatus],
    stage_key: str,
) -> str:
    """The group `stage_key` would empty by being pruned, or "" if none.

    Empty means the prune is safe: the stage is in no group, or a sibling is
    still going to run. "Still going to run" is any status other than WONT_DO —
    a PENDING sibling counts, because the refusal is about not removing the last
    candidate, not about proving one has already succeeded.
    """
    stage = next((s for s in stages if s.key == stage_key), None)
    if stage is None or not stage.alternative_group:
        return ""
    survivors = [
        member
        for member in group_members(stages, stage.alternative_group)
        if member.key != stage_key
        and stage_map.get(member.key, StageStatus.PENDING) != StageStatus.WONT_DO
    ]
    return "" if survivors else stage.alternative_group


def emptied_groups(
    stages: list[WorkflowStageDef],
    stage_map: dict[str, StageStatus],
) -> list[str]:
    """Names of every alternative group whose members are all WONT_DO.

    Read by `_derive_ticket_state`, which would otherwise call such a ticket
    DONE: group members must be `optional` to be prunable at all, so the
    required-stage filter never sees them.
    """
    groups = {s.alternative_group for s in stages if s.alternative_group}
    return sorted(
        group
        for group in groups
        if all(
            stage_map.get(member.key, StageStatus.PENDING) == StageStatus.WONT_DO
            for member in group_members(stages, group)
        )
    )
