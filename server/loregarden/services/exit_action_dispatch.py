"""Bind a dispatch to the exit actions its runtime can execute.

One entry point for every driver — built-in, manual, and external-MCP runs all
arrive here — so which harness opened the run cannot change *how* an action is
evaluated. What differs is what this process can observe: it probes its own
adapter and environment, which is the runtime of a built-in or manual run and
not of an external harness. An external harness reports nothing about its
machine, so its capabilities and credentials are recorded as unobserved and
resolve to unknown. Authority is server policy, and is probed here for all.
"""

from __future__ import annotations

from loregarden.models.domain import (
    AgentRun,
    CliAdapter,
    OrchestrationDriver,
    OrchestrationRun,
    RuntimeExitActionSnapshot,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.exit_action_ledger import (
    continuation_for_dispatch,
    overlay_granted_authority,
)
from loregarden.services.exit_action_probe import (
    probe_authority,
    probe_capabilities,
    probe_credentials,
)
from loregarden.services.exit_actions import (
    assign_actions_to_run,
    record_runtime_snapshot,
    resolve_exit_actions,
)
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from sqlmodel import Session

#: Drivers whose agent runs on a machine this process cannot observe.
_UNOBSERVED_RUNTIME_DRIVERS = frozenset({OrchestrationDriver.EXTERNAL_MCP})


def driver_for_run(session: Session, run: AgentRun) -> OrchestrationDriver:
    """Resolve the harness that opened this run; manual when there is none."""
    if not run.orchestration_run_id:
        return OrchestrationDriver.MANUAL_STAGE
    orch = session.get(OrchestrationRun, run.orchestration_run_id)
    if orch is None or orch.driver is None:
        return OrchestrationDriver.MANUAL_STAGE
    return orch.driver


def observe_runtime_snapshot(
    session: Session,
    *,
    run_id: str,
    agent_id: str,
    agent_version: int | None,
    driver: OrchestrationDriver,
    adapter: CliAdapter,
    workspace: Workspace | None,
    ticket_id: str | None,
    stage_key: str,
) -> RuntimeExitActionSnapshot:
    """What this process can observe about a run's runtime right now.

    Pure: nothing is persisted, so a recheck can observe without rewriting the
    record of the run that raised the gate.
    """
    profile = resolve_orchestration_profile(workspace) if workspace is not None else None
    authority = overlay_granted_authority(
        session, probe_authority(profile=profile), ticket_id=ticket_id, stage_key=stage_key
    )
    observed = driver not in _UNOBSERVED_RUNTIME_DRIVERS
    return RuntimeExitActionSnapshot(
        run_id=run_id,
        agent_id=agent_id,
        agent_version=agent_version,
        adapter=adapter,
        driver=driver,
        # No report arrived from the harness, so there is no current one.
        capability_data_fresh=observed,
        capabilities=probe_capabilities(adapter=adapter) if observed else {},
        credentials=probe_credentials() if observed else {},
        authority=authority,
    )


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

    A continuation of a cleared gate is assigned only what its chain has not
    already settled — the actions the clearing made executable — so an
    attested action is not re-run and an approved judgment is not re-asked.
    """
    snapshot = observe_runtime_snapshot(
        session,
        run_id=run.id,
        agent_id=run.agent_id,
        agent_version=run.agent_version,
        driver=driver_for_run(session, run),
        adapter=adapter,
        workspace=workspace,
        ticket_id=run.ticket_id,
        stage_key=run.stage_key,
    )
    record_runtime_snapshot(run, snapshot)
    resolution = resolve_exit_actions(stage_def, snapshot)
    continuation = continuation_for_dispatch(session, run)
    if continuation is not None:
        settled = {*continuation.settled_action_keys, *continuation.approved_judgment_keys}
        resolution.assigned_actions = [
            action for action in resolution.assigned_actions if action.action_key not in settled
        ]
    assign_actions_to_run(run, resolution)
    session.add(run)
    session.commit()
    return [action.model_dump(mode="json") for action in resolution.assigned_actions]
