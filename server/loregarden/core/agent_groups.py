"""Agent groups: consecutive stages one CLI run works through as one turn.

A stage's `agent_group` names the group (see `WorkflowStageDef.agent_group`);
`""` stands alone. Distinct from the *alternative* groups of
`core.stage_groups`, which are about pruning, not dispatch.

Pure functions over stage definitions and a status map: no session, no
services. Every group decision (which steps a run covers, what the combined
end-of-group gate checks, what a mid-group step is judged against) is built
from these.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from loregarden.models.domain import StageStatus, WorkflowStageDef

#: Statuses that settle a member for the purpose of ending a group.
_SETTLED = frozenset({StageStatus.DONE, StageStatus.WONT_DO})


def group_span(stages: list[WorkflowStageDef], stage_key: str) -> list[WorkflowStageDef]:
    """Every member of `stage_key`'s agent group, in template order.

    `[]` when `stage_key` is unknown or stands alone.
    """
    stage = next((s for s in stages if s.key == stage_key), None)
    if stage is None or not stage.agent_group:
        return []
    return sorted((s for s in stages if s.agent_group == stage.agent_group), key=lambda s: s.order)


def span_complete(span: Sequence[WorkflowStageDef], stage_map: Mapping[str, StageStatus]) -> bool:
    """True iff every member is DONE or WONT_DO and at least one did work (DONE).

    A member missing from `stage_map` counts as PENDING.
    """
    statuses = [stage_map.get(member.key, StageStatus.PENDING) for member in span]
    return all(status in _SETTLED for status in statuses) and StageStatus.DONE in statuses


def _ordered_union(lists: Sequence[Sequence[str]]) -> list[str]:
    """Every string, first occurrence kept, in the order the lists give them."""
    return list(dict.fromkeys(item for items in lists for item in items))


def combined_gate_def(members: Sequence[WorkflowStageDef]) -> WorkflowStageDef:
    """The last member, judged against the union of every member's gate.

    `gate_commands`, `required_evidence` and `checklist` are each the ordered,
    de-duplicated union over `members`. Refuses an empty `members`: a gate over
    nothing is a gate that silently checks nothing.
    """
    if not members:
        raise ValueError("combined_gate_def needs at least one member")
    return members[-1].model_copy(
        update={
            "gate_commands": _ordered_union([m.gate_commands for m in members]),
            "required_evidence": _ordered_union([m.required_evidence for m in members]),
            "checklist": _ordered_union([m.checklist for m in members]),
        }
    )


def mid_group_step_def(stage_def: WorkflowStageDef) -> WorkflowStageDef:
    """A grouped step judged before the group ends: its gates are deferred to the end."""
    return stage_def.model_copy(
        update={"gate_commands": [], "required_evidence": [], "checklist": []}
    )


def group_timeout(budgets: Sequence[int | None]) -> int | None:
    """The sum of the steps' budgets; unbounded (`None`) if any step is."""
    if not budgets:
        raise ValueError("group_timeout needs at least one step budget")
    total = 0
    for budget in budgets:
        if budget is None:
            return None
        total += budget
    return total
