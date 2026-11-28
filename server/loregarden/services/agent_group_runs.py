"""The run side of an agent group: what a group run covers, and where it is.

A group run is one `AgentRun` that works several consecutive steps
(`core.agent_groups`). It records its group (`agent_group_key`) and the steps
it was given (`covered_stage_keys_json`), and its `stage_key` moves with the
agent: each time the agent passes a step through `complete_stage`, the run's
identity advances to the next covered step still PENDING. Every existing
completion path then settles the step actually in progress.

Split from `agent_group_dispatch` because these are called from
`orchestration`, `orchestration_callbacks` and `run_completion`, which the gate
machinery `agent_group_dispatch` needs imports in turn.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from loregarden.core.agent_groups import combined_gate_def, group_span, mid_group_step_def
from loregarden.mcp.caller import GROUP_STAGE_KEYS_ENV
from loregarden.models.domain import AgentRun, RunStatus, StageStatus, Ticket, WorkflowStageDef
from loregarden.services.gate_dispatch_check import GateCriteriaUnanswered, refuse_unchecked_gate
from sqlmodel import Session, col, select


def run_covered_keys(run: AgentRun) -> list[str]:
    """The step keys a group run was given, in order; `[]` for an ungrouped run."""
    return list(json.loads(run.covered_stage_keys_json or "[]"))


def group_run_env(run: AgentRun) -> dict[str, str]:
    """The environment a group run's CLI process gets; empty for an ungrouped run."""
    covered = run_covered_keys(run)
    return {GROUP_STAGE_KEYS_ENV: ",".join(covered)} if covered else {}


def group_run_stamp(
    stages: list[WorkflowStageDef], target_def: WorkflowStageDef, group_stage_keys: Sequence[str]
) -> tuple[str, str]:
    """`(agent_group_key, covered_stage_keys_json)` for a run starting at `target_def`.

    Empty keys stamp an ungrouped run, even on a grouped step: only the builtin
    driver dispatches groups. Otherwise the keys must be a run of the target's
    group starting at the target, in template order, without duplicates.
    """
    keys = list(group_stage_keys)
    if not keys:
        return "", "[]"
    group = target_def.agent_group
    span_keys = [member.key for member in group_span(stages, target_def.key)]
    positions = [span_keys.index(key) if key in span_keys else -1 for key in keys]
    valid = (
        bool(group)
        and keys[0] == target_def.key
        and -1 not in positions
        and positions == sorted(set(positions))
    )
    if not valid:
        raise ValueError(
            f"group_stage_keys {keys} is not a run of agent group '{group}' starting at "
            f"'{target_def.key}'"
        )
    return group, json.dumps(keys)


def refuse_grouped_fanout(stage_def: WorkflowStageDef | None) -> None:
    """Refuse fanning out a grouped step: its run is the group's, not one attempt's."""
    if stage_def is not None and stage_def.agent_group:
        raise ValueError(
            f"Stage '{stage_def.key}' is in agent group '{stage_def.agent_group}'; a grouped "
            "step cannot fan out."
        )


def live_group_run(session: Session, ticket_id: str) -> AgentRun | None:
    """The ticket's newest RUNNING group run, or None."""
    return session.exec(
        select(AgentRun)
        .where(
            AgentRun.ticket_id == ticket_id,
            AgentRun.status == RunStatus.RUNNING,
            col(AgentRun.agent_group_key) != "",
        )
        .order_by(col(AgentRun.created_at).desc())
    ).first()


def group_run_for_completion(session: Session, ticket_id: str, stage_key: str) -> AgentRun | None:
    """The live group run completing `stage_key`, or None when no group run is live.

    Refuses completing any step but the one the live group run is on: an
    uncovered step, or a covered one out of order.
    """
    run = live_group_run(session, ticket_id)
    if run is None or stage_key == run.stage_key:
        return run
    covered = ", ".join(run_covered_keys(run))
    raise ValueError(
        f"Stage '{stage_key}' is not the step in progress for agent group "
        f"'{run.agent_group_key}': run {run.run_code} is on '{run.stage_key}' "
        f"(covers {covered}). Complete the steps in order."
    )


def judged_stage_def(
    stages: list[WorkflowStageDef], stage_def: WorkflowStageDef, *, run: AgentRun | None
) -> WorkflowStageDef:
    """The definition a step's verdict is judged against.

    An ungrouped run (or none) is judged against the step as written. Inside a
    group run, a middle step is judged without its gates (they are deferred),
    and the last step against the union of the whole group's, so the agent
    learns in its own turn that combined evidence is missing.
    """
    if run is None or not run.agent_group_key:
        return stage_def
    span = group_span(stages, stage_def.key)
    if not span:
        return stage_def
    if span[-1].key == stage_def.key:
        return combined_gate_def(span)
    return mid_group_step_def(stage_def)


def advance_group_cursor(
    session: Session,
    ticket: Ticket,
    run: AgentRun | None,
    *,
    completed_key: str,
    stage_map: Mapping[str, StageStatus],
) -> None:
    """Move a group run's identity to its next PENDING covered step after a passed one.

    The next step's criteria check runs at the moment it would start, as
    `start_run` runs it for the first. A refusal leaves the run where it is
    (the completed step stays DONE: the group ends early) and is re-raised
    naming the step that was recorded.
    """
    if run is None or not run.agent_group_key:
        return
    covered = run_covered_keys(run)
    if completed_key not in covered:
        return
    following = covered[covered.index(completed_key) + 1 :]
    nxt = next(
        (
            key
            for key in following
            if stage_map.get(key, StageStatus.PENDING) is StageStatus.PENDING
        ),
        None,
    )
    if nxt is None:
        return
    try:
        refuse_unchecked_gate(
            session, ticket, stage_key=nxt, orchestration_run_id=run.orchestration_run_id
        )
    except GateCriteriaUnanswered as exc:
        raise GateCriteriaUnanswered(
            f"Step '{completed_key}' is recorded; the group run stops here: {exc}"
        ) from exc
    run.stage_key = nxt
    session.add(run)
    session.commit()
