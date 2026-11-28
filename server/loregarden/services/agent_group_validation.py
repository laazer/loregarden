"""Refuse an agent group the dispatcher could not run as one CLI turn.

A group (`WorkflowStageDef.agent_group`) is dispatched as one run of one agent
CLI, on one adapter and one model, walking its steps in order, with its gates
deferred to its end. Each rule below refuses a shape that would break one of
those promises at run time instead of at save time. Run on Studio create,
update (stages or transitions) and publish, after `validate_stage_agent_ids`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from loregarden.agents.registry import AGENTS
from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import StudioAgent, StudioWorkflowStage, WorkflowStageDef
from loregarden.services.studio_routing import (
    TERMINAL_STAGE_KEY,
    is_agentless_stage,
    is_parallel_stage,
)
from sqlmodel import Session, col, select

_CLASSIFY_STAGE_TYPE = "classify"  # py-org: allow-string - the stage_type field is a free string
_DEFAULT_MODEL_LABEL = "(default)"


def _keys(stages: Sequence[StudioWorkflowStage]) -> str:
    return ", ".join(stage.key for stage in stages)


def _check_terminal(group: str, members: list[StudioWorkflowStage]) -> None:
    bad = [m for m in members if m.terminal or m.key == TERMINAL_STAGE_KEY]
    if bad:
        raise ValueError(
            f"Agent group '{group}' contains the terminal step(s) {_keys(bad)}; the step "
            "that ends the workflow runs no agent and cannot join a group."
        )


def _check_agentless(group: str, members: list[StudioWorkflowStage]) -> None:
    bad = [m for m in members if is_agentless_stage(m)]
    if bad:
        raise ValueError(
            f"Agent group '{group}' contains step(s) with no agent: {_keys(bad)}; every "
            "grouped step must name the agent that does its work."
        )


def _check_parallel(group: str, members: list[StudioWorkflowStage]) -> None:
    bad = [m for m in members if is_parallel_stage(m)]
    if bad:
        raise ValueError(
            f"Agent group '{group}' contains parallel step(s) {_keys(bad)}; a parallel step "
            "runs several agents at once and cannot join a group."
        )


def _check_exit_actions(group: str, members: list[StudioWorkflowStage]) -> None:
    bad = [m for m in members if m.exit_actions_enabled and m.exit_actions]
    if bad:
        raise ValueError(
            f"Agent group '{group}': step(s) {_keys(bad)} declare exit actions, which stop "
            "for a person mid-run; move the sign-off to a stage after the group."
        )


def _check_consecutive_by_order(
    group: str, members: list[StudioWorkflowStage], ordered: list[StudioWorkflowStage]
) -> None:
    keys = [stage.key for stage in ordered]
    first = keys.index(members[0].key)
    last = keys.index(members[-1].key)
    intruders = [stage for stage in ordered[first : last + 1] if stage.agent_group != group]
    if intruders:
        raise ValueError(
            f"Agent group '{group}' is not consecutive: its steps {_keys(members)} are "
            f"interrupted by {_keys(intruders)}."
        )


def _check_consecutive_on_route(
    group: str,
    members: list[StudioWorkflowStage],
    defs: list[WorkflowStageDef],
    transitions: list[dict[str, str]],
) -> None:
    for current, following in zip(members, members[1:], strict=False):
        if current.stage_type == _CLASSIFY_STAGE_TYPE:
            continue  # its branches are checked by _check_classify_exits
        route = StateMachine.resolve_next_stage_key(defs, transitions, current.key, outcome="pass")
        to_key = route.to_key if route else ""
        if to_key != following.key:
            raise ValueError(
                f"Agent group '{group}' is not consecutive on the workflow's route: a pass "
                f"from '{current.key}' goes to '{to_key or '(nowhere)'}', not to the group's "
                f"next step '{following.key}'."
            )


def _member_agent_ids(member: StudioWorkflowStage) -> list[str]:
    ids = [member.agent_id] if member.agent_id else []
    ids += [route.agent_id for route in member.classify_routes if route.agent_id]
    return list(dict.fromkeys(ids))


def _studio_agents(session: Session, members: list[StudioWorkflowStage]) -> dict[str, StudioAgent]:
    ids = {agent_id for member in members for agent_id in _member_agent_ids(member)}
    if not ids:
        return {}
    rows = session.exec(select(StudioAgent).where(col(StudioAgent.slug).in_(ids))).all()
    return {row.slug: row for row in rows}


def _check_one_value(
    group: str,
    members: list[StudioWorkflowStage],
    values_of: Callable[[StudioWorkflowStage], list[str]],
    noun: str,
) -> None:
    per_member = [(member.key, values_of(member)) for member in members]
    distinct = {value for _key, values in per_member for value in values}
    if len(distinct) > 1:
        listing = ", ".join(f"{key}: {'/'.join(values)}" for key, values in per_member)
        raise ValueError(
            f"Agent group '{group}' mixes {noun}s ({listing}); a group is one CLI run on "
            f"one {noun}."
        )


def _check_adapters_and_models(
    session: Session, group: str, members: list[StudioWorkflowStage]
) -> None:
    rows = _studio_agents(session, members)

    def adapter_of(agent_id: str) -> str:
        row = rows.get(agent_id)
        if row is not None:
            return row.adapter
        return str(AGENTS.get(agent_id, {}).get("adapter", ""))

    def adapters(member: StudioWorkflowStage) -> list[str]:
        return list(dict.fromkeys(adapter_of(agent_id) for agent_id in _member_agent_ids(member)))

    def models(member: StudioWorkflowStage) -> list[str]:
        if member.model:
            return [member.model]
        found = [
            (rows[agent_id].default_model if agent_id in rows else "") or _DEFAULT_MODEL_LABEL
            for agent_id in _member_agent_ids(member)
        ]
        return list(dict.fromkeys(found)) or [_DEFAULT_MODEL_LABEL]

    _check_one_value(group, members, adapters, "adapter")
    _check_one_value(group, members, models, "model")


def _check_classify_exits(
    group: str, members: list[StudioWorkflowStage], ordered: list[StudioWorkflowStage]
) -> None:
    keys = [stage.key for stage in ordered]
    last_index = keys.index(members[-1].key)
    after = keys[last_index + 1] if last_index + 1 < len(keys) else ""
    for position, member in enumerate(members[:-1]):
        if member.stage_type != _CLASSIFY_STAGE_TYPE:
            continue
        later = [m.key for m in members[position + 1 :]]
        allowed = {*later, after} - {""}
        for route in member.classify_routes:
            if route.to_stage and route.to_stage not in allowed:
                raise ValueError(
                    f"Agent group '{group}': classify step '{member.key}' routes to "
                    f"'{route.to_stage}', which leaves the group from its middle; route only "
                    f"to a later step of the group ({', '.join(later) or 'none'}) or to the "
                    f"step right after it ('{after or 'none'}')."
                )


def validate_agent_groups(
    session: Session,
    stages: list[StudioWorkflowStage],
    transitions: list[dict[str, str]],
) -> None:
    """Raise `ValueError` naming the group and steps of the first invalid agent group."""
    ordered = sorted(stages, key=lambda stage: stage.order)
    groups = sorted({stage.agent_group for stage in ordered if stage.agent_group})
    if not groups:
        return
    defs = [WorkflowStageDef.model_validate(stage.model_dump()) for stage in ordered]
    for group in groups:
        members = [stage for stage in ordered if stage.agent_group == group]
        _check_terminal(group, members)
        _check_agentless(group, members)
        _check_parallel(group, members)
        _check_exit_actions(group, members)
        _check_consecutive_by_order(group, members, ordered)
        _check_consecutive_on_route(group, members, defs, transitions)
        _check_adapters_and_models(session, group, members)
        _check_classify_exits(group, members, ordered)
