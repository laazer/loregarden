"""Apply workflow transition plans to tickets (forward advance and upstream rework)."""

from __future__ import annotations

import logging

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

logger = logging.getLogger(__name__)


def routes_forward(stages: list[WorkflowStageDef], from_key: str, to_key: str) -> bool:
    """True when `to_key` sits after `from_key` — the one direction rework may not go.

    Equal keys are deliberately allowed: the gate-autofix path re-routes a stage to
    itself so the same agent redoes it with the gate errors injected.
    """
    ordered = [stage.key for stage in sorted(stages, key=lambda s: s.order)]
    if from_key not in ordered or to_key not in ordered:
        return False
    return ordered.index(to_key) > ordered.index(from_key)


def valid_route_targets(
    stages: list[WorkflowStageDef],
    from_key: str,
    outcome: str = "pass",
) -> list[str]:
    """Stage keys an agent may legally name as a route target from `from_key`.

    `next_stage_key` is documented as an upstream-rework hint, so on a pass the
    workflow itself decides the next stage and there is nothing valid to name.
    Includes `from_key` itself — a stage redoing its own work is a valid route.
    """
    if outcome != "reject":
        return []
    ordered = [stage.key for stage in sorted(stages, key=lambda s: s.order)]
    if from_key not in ordered:
        return []
    return ordered[: ordered.index(from_key) + 1]


def _validate_route_target(
    stages: list[WorkflowStageDef],
    stage_keys: set[str],
    ticket_id: str,
    *,
    from_key: str,
    outcome: str,
    next_stage_key: str,
    strict: bool,
) -> str:
    """Vet an agent-supplied route target; return why it was discarded ("" if legal).

    An agent naming a stage key this workflow doesn't have (the stage-report
    contract invites a guess, and models reach for plausible names like
    "implementation" over the real "implement") used to be taken at face value,
    parking the cursor on a phantom stage: reset_upstream_stages no-ops on an
    unknown target, then reconcile_workflow_state quietly snaps the cursor to the
    first PENDING stage — so the rework silently went nowhere.
    """
    if next_stage_key not in stage_keys:
        reason = f"Unknown target stage '{next_stage_key}'"
    elif outcome != "reject":
        # Honoring a forward target let an agent jump the queue and skip stages
        # outright; the contract reserves this field for rework.
        reason = (
            f"next_stage_key '{next_stage_key}' is only valid with outcome='reject' "
            "(upstream rework); on a pass the workflow decides the next stage"
        )
    elif routes_forward(stages, from_key, next_stage_key):
        # Mirrors the human workflow-gate check, which has always enforced this.
        reason = f"Rework stage '{next_stage_key}' must not come after stage '{from_key}'"
    else:
        return ""

    if strict:
        targets = valid_route_targets(stages, from_key, outcome)
        hint = ", ".join(targets) if targets else "(none — omit next_stage_key)"
        raise ValueError(f"{reason}. Valid targets from '{from_key}': {hint}")
    logger.warning("Discarding invalid next_stage_key on ticket %s: %s", ticket_id, reason)
    return reason


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
    strict: bool = False,
) -> StageRoutePlan:
    """Move the workflow cursor for `ticket`.

    `strict` is for callers whose caller can still react — an agent calling
    `loregarden_complete_stage` gets the ValueError back as a tool error and can
    retry with a valid key. Post-run callers (the stdout stage report) leave it
    off: by then the run is over and nothing can retry, so a bad hint is dropped
    and logged rather than raised.
    """
    stage_keys = {stage.key for stage in stages}
    if strict and from_key not in stage_keys:
        raise ValueError(f"Unknown stage key: {from_key}")

    misroute_reason = ""
    if next_stage_key:
        misroute_reason = _validate_route_target(
            stages,
            stage_keys,
            ticket.id,
            from_key=from_key,
            outcome=outcome,
            next_stage_key=next_stage_key,
            strict=strict,
        )
        if misroute_reason:
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
        if misroute_reason:
            note = f"({misroute_reason}; routed to '{plan.to_key}' instead.)"
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
