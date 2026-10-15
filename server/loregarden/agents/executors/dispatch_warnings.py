"""The warnings a run's log opens with, before the agent speaks.

Extracted from `agents.executors.cli` when the target-branch refresh this
ticket added to `render_stage_prompt` (lg-milestone-that-917) took
`CliAgentExecutor` past the organization gate's 1000-line class cap. This is
the cohesive piece to move: all four functions take only what they are handed,
touch no executor state, and answer one question — what does the operator need
told at the top of the log that the agent itself will never say.
"""

import json
import logging

from loregarden.agents.cli_adapters import CliInvocation
from loregarden.models.domain import AgentRun, WorkflowStageDef, Workspace
from loregarden.services.cli_settings import (
    WorkspaceRuntimeSettings,
    adapter_model_pins_apply,
    adapter_override_warning,
    adapter_pin_source,
    resolve_model_for_adapter,
    weak_mcp_model_warning,
)
from loregarden.services.run_log_stream import RunLogStreamer

logger = logging.getLogger(__name__)


def warn_before_run(
    *,
    streamer: RunLogStreamer,
    run: AgentRun,
    agent: dict,
    workspace: Workspace,
    ticket_runtime: WorkspaceRuntimeSettings,
    stage_def: WorkflowStageDef | None,
    invocation: CliInvocation,
) -> None:
    """Every warning a run's log should open with, before the agent speaks."""
    maybe_warn_dispatch_waiver(streamer=streamer, run=run)
    maybe_warn_adapter_override(
        streamer=streamer,
        run=run,
        agent=agent,
        workspace=workspace,
        ticket_runtime=ticket_runtime,
        stage_def=stage_def,
        invocation=invocation,
    )
    maybe_warn_weak_mcp_model(
        streamer=streamer,
        run=run,
        agent=agent,
        workspace=workspace,
        ticket_runtime=ticket_runtime,
        stage_def=stage_def,
        selected_adapter=invocation.adapter,
    )


def maybe_warn_adapter_override(
    *,
    streamer: RunLogStreamer,
    run: AgentRun,
    agent: dict,
    workspace: Workspace,
    ticket_runtime: WorkspaceRuntimeSettings,
    stage_def: WorkflowStageDef | None,
    invocation: CliInvocation,
) -> None:
    """Say, in the run's own log, that the agent is not running as declared.

    A workspace or ticket provider pin replaces the agent's adapter and drops
    its model pin with it; without this line the run log opens like any
    other and the operator hunts for why "the model I set" never applied.
    """
    message = adapter_override_warning(
        agent_id=run.agent_id,
        agent_adapter=agent.get("adapter", "local"),
        selected_adapter=invocation.adapter,
        pin_source=adapter_pin_source(
            workspace=workspace, ticket_adapter=ticket_runtime.cli_adapter
        ),
        dropped_model=(stage_def.model if stage_def else "") or agent.get("default_model", ""),
        model=invocation.model,
    )
    if not message:
        return
    logger.warning("run %s (%s): %s", run.run_code, run.agent_id, message)
    streamer.append("WARN", message, force=True)


def maybe_warn_weak_mcp_model(
    *,
    streamer: RunLogStreamer,
    run: AgentRun,
    agent: dict,
    workspace: Workspace,
    ticket_runtime: WorkspaceRuntimeSettings,
    stage_def: WorkflowStageDef | None,
    selected_adapter: str,
) -> None:
    if selected_adapter != "claude":
        return
    pins_apply = adapter_model_pins_apply(
        agent_adapter=agent.get("adapter", "local"),
        selected_adapter="claude",
    )
    resolved_model = resolve_model_for_adapter(
        "claude",
        workspace,
        ticket_model=ticket_runtime.claude_model,
        stage_model=(stage_def.model if stage_def and pins_apply else ""),
        agent_model=agent.get("default_model", "") if pins_apply else "",
    )
    model_warning = weak_mcp_model_warning(resolved_model, "claude")
    if not model_warning:
        return
    logger.warning("run %s (%s): %s", run.run_code, run.agent_id, model_warning)
    streamer.append("WARN", model_warning, force=True)


def maybe_warn_dispatch_waiver(*, streamer, run: AgentRun) -> None:
    """Say, in the run's own log, that a person waived a failing check.

    The alternative is a run that starts in a checkout already known to be
    broken and whose log opens exactly like a healthy one — so whoever reads
    the failure later hunts the agent's output for a cause that is sitting in
    an approval row. `_record_and_check_boundary` keeps the failing check ids
    on the run; this is the part a human actually sees.

    Both the checks and the waiver are read back off the run rather than
    threaded in, so this reports what was persisted, not what was intended.
    """
    if not run.dispatch_waiver_approval_id:
        return
    try:
        waived_checks = json.loads(run.start_preflight_failures_json or "[]")
    except json.JSONDecodeError:
        # silent-ok: the column is ours and written by json.dumps, so this is
        # unreachable short of hand-editing; degrade to naming the waiver
        # rather than losing the warning that a check was waived at all.
        waived_checks = []
    detail = ", ".join(waived_checks) or "a failing pre-dispatch check"
    message = (
        f"Dispatched with {detail} waived by approval "
        f"{run.dispatch_waiver_approval_id}. The environment was NOT fixed — "
        "a failure here may be that, not the work."
    )
    logger.warning("run %s (%s): %s", run.run_code, run.agent_id, message)
    streamer.append("WARN", message, force=True)
