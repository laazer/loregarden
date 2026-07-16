"""Apply workflow transition plans to tickets (forward advance and upstream rework)."""

from __future__ import annotations

from loregarden.core.state_machine import StageRoutePlan, StateMachine
from loregarden.models.domain import (
    OrchestrationRun,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.studio_service import resolve_stage_execution
from loregarden.services.workflow_state import (
    parse_stage_map,
    reconcile_workflow_state,
    serialize_stage_map,
    set_stage_status,
)


def previous_stage_key(stages: list[WorkflowStageDef], from_key: str) -> str | None:
    """Immediately preceding stage by `order` — last-resort reject-reroute target.

    Used only when neither an agent-specified reroute target nor a template
    `reject` transition exists, so a stage failure doesn't stall in BLOCKED
    forever just because the template is missing a rework route.
    """
    ordered = sorted(stages, key=lambda s: s.order)
    keys = [s.key for s in ordered]
    try:
        idx = keys.index(from_key)
    except ValueError:
        return None
    if idx <= 0:
        return None
    return keys[idx - 1]


def apply_stage_route(
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    transitions: list[dict[str, str]],
    *,
    from_key: str,
    outcome: str = "pass",
    next_stage_key: str = "",
    next_agent: str = "",
    blocking_issues: str = "",
    orch_run: OrchestrationRun | None = None,
) -> StageRoutePlan:
    misrouted = ""
    if next_stage_key and next_stage_key not in {stage.key for stage in stages}:
        # An agent naming a stage key this workflow doesn't have (the stage-report
        # contract invites a guess, and models reach for plausible names like
        # "implementation" over the real "implement"). Taking it at face value
        # parked the cursor on a phantom stage: reset_upstream_stages no-ops on an
        # unknown target, then reconcile_workflow_state quietly snaps the cursor to
        # the first PENDING stage — so the rework silently went nowhere. Drop the
        # bad hint and fall back to the template's reject route / previous stage,
        # surfacing the miss in blocking_issues rather than swallowing it.
        misrouted = next_stage_key
        next_stage_key = ""

    plan = StateMachine.resolve_next_stage_key(
        stages,
        transitions,
        from_key,
        outcome=outcome,
        explicit_to=next_stage_key,
    )
    if not plan and outcome == "reject":
        fallback_key = previous_stage_key(stages, from_key)
        if fallback_key:
            plan = StageRoutePlan(
                from_key=from_key,
                to_key=fallback_key,
                outcome=outcome,
                upstream=True,
            )
    if not plan:
        raise ValueError(
            f"No workflow route defined from stage '{from_key}' with outcome '{outcome}'"
        )

    stage_map = parse_stage_map(instance, stages)
    if plan.upstream or outcome == "reject":
        stage_map = StateMachine.reset_upstream_stages(
            stage_map,
            stages,
            from_key=from_key,
            to_key=plan.to_key,
        )
        instance.stages_json = serialize_stage_map(stage_map, stages)
        ticket.workflow_stage_key = plan.to_key
        ticket.workflow_stage_status = StageStatus.PENDING
        if misrouted:
            note = (
                f"(Requested rework stage '{misrouted}' is not a stage in this "
                f"workflow; routed to '{plan.to_key}' instead.)"
            )
            blocking_issues = f"{blocking_issues}\n\n{note}" if blocking_issues else note
        if blocking_issues:
            ticket.blocking_issues = blocking_issues[:2000]
        else:
            ticket.blocking_issues = ""
        ticket.next_status = "Proceed"
    else:
        if stage_map.get(from_key) != StageStatus.WONT_DO:
            set_stage_status(ticket, instance, stages, from_key, StageStatus.DONE)
        ticket.workflow_stage_key = plan.to_key
        ticket.blocking_issues = ""
        ticket.next_status = "Proceed"

    # Reconcile first: it derives workflow_stage_key/status/ticket.state from
    # the stage map and, as a side effect, backfills ticket.next_agent from
    # the current stage's static agent_id. Computing the *real* next_agent
    # below and writing it after this call ensures that backfill never
    # clobbers a deliberately-resolved value (classify routing, an explicit
    # reject-hint, or a template-declared transition agent).
    reconcile_workflow_state(ticket, instance, stages, persist=False)

    target_stage = next((stage for stage in stages if stage.key == plan.to_key), None)
    # next_agent is a hint an agent supplies when it calls complete_stage — it's
    # documented (loregarden_mcp_v1.md) as being for routing back to an upstream
    # agent on rework, not for steering a normal forward pass. Honoring it
    # unconditionally let a completing agent silently override the workflow
    # template's own agent assignment for the *next* stage (e.g. sending "review"
    # to whatever agent it named instead of the template's architecture_reviewer).
    agent_hint = next_agent if outcome == "reject" else ""
    chosen_agent = (agent_hint or plan.transition_agent_id or "").strip()
    if not chosen_agent and target_stage:
        chosen_agent, _ = resolve_stage_execution(ticket, target_stage)
    if chosen_agent:
        ticket.next_agent = chosen_agent
    elif target_stage and target_stage.agent_id:
        ticket.next_agent = target_stage.agent_id

    if orch_run is not None:
        orch_run.current_stage_key = plan.to_key
    return plan


def normalize_transitions_for_api(transitions_json: str) -> list[dict[str, str]]:
    """Normalize template transitions for API/UI (handles YAML `when` and legacy `on` quirks)."""
    items = StateMachine.parse_transitions(transitions_json)
    normalized: list[dict[str, str]] = []
    for item in items:
        when = StateMachine._transition_when(item)
        entry = {
            "from": item.get("from", ""),
            "to": item.get("to", ""),
            "when": when or "default",
        }
        agent_id = item.get("agent_id", "")
        if agent_id:
            entry["agent_id"] = agent_id
        normalized.append(entry)
    return normalized
