"""Bind a dispatch to the exit actions its runtime can execute.

One entry point for every driver — built-in, manual, and external-MCP runs all
arrive here — so which harness opened the run cannot change whether an action
is treated as executable.
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    AgentRun,
    CliAdapter,
    OrchestrationDriver,
    OrchestrationRun,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.exit_action_probe import (
    probe_authority,
    probe_capabilities,
    probe_credentials,
)
from loregarden.services.exit_actions import (
    assign_actions_to_run,
    capture_runtime_snapshot,
    resolve_exit_actions,
)
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from sqlmodel import Session


def _driver_for_run(session: Session, run: AgentRun) -> OrchestrationDriver:
    """Resolve the harness that opened this run; manual when there is none."""
    if not run.orchestration_run_id:
        return OrchestrationDriver.MANUAL_STAGE
    orch = session.get(OrchestrationRun, run.orchestration_run_id)
    if orch is None or orch.driver is None:
        return OrchestrationDriver.MANUAL_STAGE
    return orch.driver


def assign_dispatch_exit_actions(
    session: Session,
    *,
    run: AgentRun,
    workspace: Workspace,
    stage_def: WorkflowStageDef | None,
    adapter: CliAdapter,
) -> list[dict]:
    """Snapshot the runtime, assign what this run can execute, and persist both.

    Returns the assigned actions as plain payloads for the stage prompt. The
    snapshot is written even when the stage authored no actions: it is the
    record of what was true at dispatch, and a stage that gains actions later
    should not silently have no evidence for the run that preceded them.
    """
    driver = _driver_for_run(session, run)
    profile = resolve_orchestration_profile(workspace)
    snapshot = capture_runtime_snapshot(
        run=run,
        driver=driver,
        adapter=adapter,
        capability_statuses=probe_capabilities(adapter=adapter),
        credential_preflight=probe_credentials(),
        authority_statuses=probe_authority(profile=profile),
    )
    resolution = resolve_exit_actions(stage_def, snapshot)
    # A continuation dispatch carries the subset the operator just unblocked;
    # anything outside it stays with the gate rather than being re-run here.
    continuation = json.loads(run.assigned_exit_action_keys_json or "[]")
    if continuation:
        resolution.assigned_actions = [
            action for action in resolution.assigned_actions if action.action_key in continuation
        ]
    assign_actions_to_run(run, resolution)
    session.add(run)
    session.commit()
    return [action.model_dump(mode="json") for action in resolution.assigned_actions]
