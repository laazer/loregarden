"""Which stages produce a design plan, and whether a run may sign one off.

`gate_required` on `plan-synthesis` / `ui-design` (migration 0133) makes a
review point exist between a plan and its implementation. Whether a person or
the orchestrator sits at that point is the run's `approve_design_plans` dial —
on by default, turned off in the run modal when someone wants to see the plan
first. Nothing here touches any other gate: `auto_approve` remains the only
thing that signs off the final quality gate on the run's behalf.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import (
    OrchestrationRun,
    OrchestratorDecision,
    Ticket,
    WorkflowStageDef,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: The agents whose stage output is a design plan. Agent ids are registry
#: rows, not an enum we own, so the closed set lives here in one place.
DESIGN_PLAN_AGENTS = frozenset({"planner", "ui-design-decision"})  # py-org: allow-string


def is_design_plan_stage(stage: WorkflowStageDef) -> bool:
    """Whether the work this stage produces is a plan a person might want to see."""
    if stage.agent_id in DESIGN_PLAN_AGENTS:
        return True
    return any(member.agent_id in DESIGN_PLAN_AGENTS for member in stage.parallel_agents)


def orchestrator_may_sign_off(
    orch_run: OrchestrationRun, stage: WorkflowStageDef, *, auto_approve: bool
) -> bool:
    """Whether this run resolves the awaiting gate on `stage` itself.

    `auto_approve` signs off every gate, as it always has. Without it, only a
    design-plan stage's gate, and only when the run was started with
    `approve_design_plans` (the default).
    """
    if auto_approve:
        return True
    return bool(orch_run.approve_design_plans) and is_design_plan_stage(stage)


def record_design_plan_sign_off(
    session: Session, ticket: Ticket, orch_run: OrchestrationRun, stage_key: str
) -> None:
    """Say in the ticket history that the run, not a person, approved the plan."""
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.APPROVED_DESIGN_PLAN,
        stage_key=stage_key,
        reason=(
            f"Approved the design plan from '{stage_key}' on the run's behalf, as the run "
            "was started with approve_design_plans. Untick it in the run modal to see the "
            "plan first."
        ),
        evidence={"orchestration_run_code": orch_run.run_code},
    )
    session.commit()
