"""The builtin driver's side of an agent group: what to dispatch, and when to gate it.

A group (`core.agent_groups`) is dispatched by `BuiltinOrchestrator` as one
run covering every remaining member from the first incomplete one
(`covered_stage_keys`), with a budget that is the sum of theirs
(`dispatch_timeout_seconds`). Its gates run once, when every member is
settled, whichever run settled the last one (`settle_group`). External drivers
(MCP `start_stage`, the autopilot skill) still dispatch one step per run.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from loregarden.core.agent_groups import (
    combined_gate_def,
    group_span,
    group_timeout,
    span_complete,
)
from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.agent_group_runs import run_covered_keys
from loregarden.services.gate_recovery import GateDecision, GateRecovery
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.stage_timeouts import stage_timeout_seconds
from loregarden.services.studio_routing import should_skip_stage
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, col, select

_SETTLED = frozenset({StageStatus.DONE, StageStatus.WONT_DO})


class GroupSettlement(Enum):
    """What the builtin loop does after a grouped step's run returned."""

    INCOMPLETE = "incomplete"  # a member is not settled: no gate, re-pick the first one
    PASSED = "passed"  # the combined gate passed (or there was nothing to gate)
    REROUTED = "rerouted"  # the combined gate failed: the group was sent back from its head
    BLOCKED = "blocked"  # the combined gate failed and the ticket is blocked
    NO_ROUTE = "no_route"  # the group's last step has nowhere to go on a pass


def covered_stage_keys(
    ticket: Ticket,
    stages: list[WorkflowStageDef],
    stage_map: Mapping[str, StageStatus],
    start_key: str,
) -> list[str]:
    """The steps one run starting at `start_key` is given, in order; `[]` if ungrouped.

    From `start_key` to the group's end, leaving out members whose `skip_when`
    this ticket satisfies and members already settled. `start_key` is always
    first.
    """
    span = group_span(stages, start_key)
    if not span:
        return []
    keys = [member.key for member in span]
    remaining = span[keys.index(start_key) :]
    return [start_key] + [
        member.key
        for member in remaining[1:]
        if stage_map.get(member.key, StageStatus.PENDING) not in _SETTLED
        and not should_skip_stage(ticket, member, stages)
    ]


def dispatch_timeout_seconds(
    ticket: Ticket,
    stages: list[WorkflowStageDef],
    stage_map: Mapping[str, StageStatus],
    stage_def: WorkflowStageDef,
    run_default: int | None,
) -> int | None:
    """The agent budget of the run dispatched for `stage_def`: a group's is the sum of its steps'."""
    covered = covered_stage_keys(ticket, stages, stage_map, stage_def.key)
    if not covered:
        return stage_timeout_seconds(stage_def, run_default)
    by_key = {stage.key: stage for stage in stages}
    return group_timeout([stage_timeout_seconds(by_key[key], run_default) for key in covered])


def group_keys_for_dispatch(
    orch: OrchestrationService, ticket: Ticket, target_key: str
) -> list[str]:
    """The steps the builtin's run for `target_key` covers.

    `[]` when the step is ungrouped, or carries a scope-reroute pin: the sibling
    specialist runs that step alone, and the group's gate still runs once the
    span completes (`settle_group`). A repair turn covers the group like any
    other dispatch of it: the repair agent picks up the group's remaining work.
    """
    if ticket.scope_reroute_agent:
        return []
    instance, stages = orch._resolve_stages(ticket)  # noqa: SLF001 - the driver's own resolver
    if instance is None:
        return []
    return covered_stage_keys(ticket, stages, parse_stage_map(instance, stages), target_key)


def effective_stage_key(session: Session, ticket: Ticket, target_key: str) -> str:
    """The step the run just dispatched for `target_key` ended on.

    A group run's `stage_key` moves with the agent, so the step the loop must
    ask about after the run (re-armed? repair-pinned?) is the run's, not the
    one it was dispatched for. The newest run on the ticket is that run.
    """
    newest = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id)
        .order_by(col(AgentRun.created_at).desc())
    ).first()
    if newest is None or not newest.agent_group_key or target_key not in run_covered_keys(newest):
        return target_key
    return newest.stage_key


_DECISION_SETTLEMENT = {
    GateDecision.PASS: GroupSettlement.PASSED,
    GateDecision.REROUTED: GroupSettlement.REROUTED,
    GateDecision.BLOCKED: GroupSettlement.BLOCKED,
}


def settle_group(
    gates: GateRecovery,
    ticket: Ticket,
    profile: OrchestrationProfile,
    stage_def: WorkflowStageDef,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    orch_run: OrchestrationRun,
    *,
    transitions: list[dict[str, str]],
) -> GroupSettlement:
    """Run the group's combined gate once every member is settled, and say what follows.

    An incomplete span runs nothing. A complete one runs the union of the
    settled members' gates once, from the last member that did work, and a
    failure sends the group back from its head (`GateRecovery._reroute_group`).
    """
    span = group_span(stages, stage_def.key)
    stage_map = parse_stage_map(instance, stages)
    if not span_complete(span, stage_map):
        return GroupSettlement.INCOMPLETE
    done = [member for member in span if stage_map.get(member.key) is StageStatus.DONE]
    last_done = done[-1].key
    route = StateMachine.resolve_next_stage_key(stages, transitions, last_done, outcome="pass")
    if route is None:
        return GroupSettlement.NO_ROUTE
    decision = gates.run_gates_with_autofix(
        ticket,
        profile,
        combined_gate_def(done),
        instance,
        stages,
        orch_run,
        from_stage=last_done,
        to_stage=route.to_key,
        group_head=span[0].key,
    )
    if decision is GateDecision.PASS:
        # Past the group, so the ticket reads as standing on the step after it
        # (the way a passed ungrouped step's cursor moves under `complete_stage`).
        apply_stage_route(
            ticket, instance, stages, transitions, from_key=last_done, orch_run=orch_run
        )
        gates.session.add(ticket)
        gates.session.add(instance)
        gates.session.commit()
    return _DECISION_SETTLEMENT[decision]
