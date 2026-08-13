"""Run a whole ticket from a coding harness outside this control plane.

An operator copies a ticket prompt out of the UI into their own Claude Code or
Codex session. That session then drives the ticket over MCP: it opens an
orchestration run, checks each stage out, does the work, and hands the stage
report back. Loregarden records the same things it records for its own runs —
stage transitions, artifacts, start and finish times — plus *which* harness did
it, so an outside run is comparable against a control-plane one.

Two rules separate this path from the built-in driver:

- **No queue.** The lane pool exists to bound how many agents this machine
  spawns. An external harness spawns nothing here, so reserving a lane for it
  would idle a slot that could be running real work — and make the operator wait
  on capacity this run does not consume.
- **No supervision.** There is no child process to orphan, so these runs are
  exempt from the restart reapers (see ``fail_interrupted_runs``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loregarden.agents.mcp_context import resolve_mcp_url
from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    ExternalHarnessPromptView,
    ExternalStageResultView,
    ExternalStageView,
    OrchestrationDriver,
    OrchestrationRun,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.run_service import RunService
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

#: Marks an agent run that a harness outside this process executed. Mirrors
#: ``TERMINAL_HANDOFF_COMMAND_PREFIX``: there is no argv to record, so the
#: command field carries what ran it instead of a lie about a subprocess.
EXTERNAL_HARNESS_COMMAND_PREFIX = "[external-harness]"

HARNESS_LABELS: dict[ExternalHarness, str] = {
    ExternalHarness.CLAUDE_CODE: "Claude Code",
    ExternalHarness.CODEX: "Codex",
    ExternalHarness.CURSOR: "Cursor",
    ExternalHarness.OTHER: "an external coding harness",
}

_GATE_MESSAGE = (
    "This stage runs no agent — it is a human approval gate. Loregarden has "
    "opened it in the approval inbox; stop here and tell the operator."
)
_FINISHED_MESSAGE = (
    "The workflow has no stage left to run — it reached its terminal stage. "
    "Finish by calling loregarden_complete_orchestration."
)


def _workspace_for(session: Session, ticket: Ticket) -> Workspace:
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise ValueError(f"Unknown workspace for ticket: {ticket.id}")
    return workspace


def _workflow_finished(ticket: Ticket) -> bool:
    """Whether there is nothing left for the harness to run.

    Reaching the terminal stage is what finalizes the ticket (see
    ``OrchestrationService.finalize_workflow``), so the ticket's own state is the
    signal — a stage cursor can sit on the terminal key either side of that.
    """
    return ticket.state in StateMachine.TERMINAL_TICKET_STATES


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def start_external_orchestration(
    session: Session, ticket: Ticket, *, harness: ExternalHarness
) -> OrchestrationRun:
    """Open an orchestration run driven by ``harness``, without taking a lane."""
    workspace = _workspace_for(session, ticket)
    profile = resolve_orchestration_profile(workspace)
    return OrchestrationCallbackService(session).start_orchestration_run(
        ticket,
        driver=OrchestrationDriver.EXTERNAL_MCP,
        profile_slug=profile.slug,
        external_harness=harness,
    )


def begin_external_stage(
    session: Session, orch_run: OrchestrationRun, *, stage_key: str | None = None
) -> ExternalStageView:
    """Check the next stage out to the harness driving ``orch_run``.

    Returns the prompt Loregarden's own agent would have been given for that
    stage, so the harness runs the same instructions rather than an improvised
    reading of the ticket.
    """
    ticket = session.get(Ticket, orch_run.ticket_id)
    if not ticket:
        raise ValueError(f"Ticket not found: {orch_run.ticket_id}")
    if orch_run.external_harness is None:
        raise ValueError(
            "This orchestration run was not opened by an external harness — "
            "start one with loregarden_start_orchestration and external_harness set."
        )

    run_svc = RunService(session)
    run = run_svc.start_stage_execution(ticket, stage_key=stage_key)
    session.refresh(ticket)
    if run is None:
        # An agentless stage: a human approval gate, or the terminal stage,
        # which start_stage_execution has already finalized.
        return ExternalStageView(
            stage_key=stage_key or ticket.workflow_stage_key,
            message=_FINISHED_MESSAGE if _workflow_finished(ticket) else _GATE_MESSAGE,
        )

    run.orchestration_run_id = orch_run.id
    run.external_harness = orch_run.external_harness
    session.add(run)
    session.commit()

    prompt, repo_root = run_svc.executor.render_stage_prompt(run, ticket)
    run.command = f"{EXTERNAL_HARNESS_COMMAND_PREFIX} {orch_run.external_harness.value}"
    orch_run.current_stage_key = run.stage_key
    session.add(run)
    session.add(orch_run)
    session.commit()
    session.refresh(run)

    stage_views = OrchestrationService(session).build_stage_views(ticket)
    stage_view = next((s for s in stage_views if s.key == run.stage_key), None)
    return ExternalStageView(
        agent_run_id=run.id,
        run_code=run.run_code,
        stage_key=run.stage_key,
        stage_name=stage_view.name if stage_view else run.stage_key,
        agent_id=run.agent_id,
        skill_name=run.skill_name,
        prompt=prompt,
        repo_path=str(repo_root),
        started_at=_as_utc(run.started_at),
    )


def finish_external_stage(
    session: Session, run: AgentRun, *, transcript: str, failed: bool = False
) -> ExternalStageResultView:
    """Settle a stage the harness has finished, routing on its stage report.

    ``transcript`` goes through the same parser a supervised run's stdout does,
    so a `<<<LOREGARDEN_STAGE_REPORT>>>` block from an outside harness reroutes,
    blocks or advances the workflow identically.
    """
    if run.external_harness is None:
        raise ValueError(f"Run {run.run_code} was not checked out to an external harness")

    started_at = _as_utc(run.started_at) or _as_utc(run.created_at)
    orch = OrchestrationService(session)
    run = orch.complete_run(
        run,
        status=RunStatus.FAILED if failed else RunStatus.SUCCEEDED,
        stdout=transcript,
    )
    session.refresh(run)
    ticket = session.get(Ticket, run.ticket_id)
    if not ticket:
        raise ValueError(f"Ticket not found: {run.ticket_id}")

    finished_at = _as_utc(run.finished_at) or datetime.now(timezone.utc)
    duration = (finished_at - started_at).total_seconds() if started_at else 0.0
    return ExternalStageResultView(
        agent_run_id=run.id,
        stage_key=run.stage_key,
        status=run.status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(duration, 3),
        workflow_stage_key=ticket.workflow_stage_key,
        workflow_stage_status=ticket.workflow_stage_status,
        ticket_state=ticket.state,
        blocking_issues=ticket.blocking_issues,
        workflow_finished=_workflow_finished(ticket),
    )


def build_external_harness_prompt(
    session: Session, ticket: Ticket, *, harness: ExternalHarness
) -> ExternalHarnessPromptView:
    """The text an operator pastes into ``harness`` to run this ticket there."""
    workspace = _workspace_for(session, ticket)
    orch = OrchestrationService(session)
    stages = orch.build_stage_views(ticket)
    template = orch.get_template_for_ticket(ticket)
    criteria = json.loads(ticket.acceptance_criteria_json or "[]")
    label = HARNESS_LABELS[harness]

    lines = [
        f"# Loregarden ticket handoff — {ticket.external_id}: {ticket.title}",
        "",
        f"You are running this ticket in **{label}**, outside Loregarden's own",
        "orchestrator. Loregarden is the control plane: it owns the ticket, the workflow",
        "stages, and the run record. You drive the pipeline; it records what happened.",
        "",
        "## Ticket",
        "",
        f"- workspace_slug: `{workspace.slug}`",
        f"- external_id: `{ticket.external_id}`",
        f"- ticket_id: `{ticket.id}`",
        f"- repo path: `{resolve_workspace_root(workspace)}`",
        f"- workflow: `{template.slug if template else '—'}`",
        f"- current stage: `{ticket.workflow_stage_key or '—'}`",
        f"- state: `{ticket.state.value}`",
        "",
        "There is no ticket markdown file anywhere in the repo. Everything below comes",
        "from Loregarden's database, and every update goes back the same way.",
        "",
        "### Description",
        "",
        ticket.description or "_(empty — read the ticket over MCP before assuming a scope)_",
        "",
        "### Acceptance criteria",
        "",
        *([f"- {item}" for item in criteria] or ["_(none recorded)_"]),
        "",
        "### Workflow stages",
        "",
        "| # | key | name | agent | status |",
        "| - | --- | ---- | ----- | ------ |",
        *[
            f"| {i + 1} | `{s.key}` | {s.name} | {s.agent_id or '—'} | {s.status.value} |"
            for i, s in enumerate(stages)
        ],
        "",
        "## Connect to Loregarden",
        "",
        f"Loregarden's MCP server is at `{resolve_mcp_url()}` (HTTP). Add it before you start:",
        "",
        "```bash",
        f"claude mcp add --transport http loregarden {resolve_mcp_url()}",
        "```",
        "",
        "In Claude Code the tools are named `mcp__loregarden__<tool>`; other harnesses use",
        "the bare names below. Do not call the MCP endpoint with curl or hand-rolled",
        "JSON-RPC — use your harness's own MCP client.",
        "",
        "## The loop you must run",
        "",
        "**1. Open the run.**",
        "",
        "```",
        "loregarden_start_orchestration",
        f'  ticket_id="{ticket.id}"',
        f'  external_harness="{harness.value}"',
        "```",
        "",
        "Keep the returned `run_id`. Passing `external_harness` is what marks every stage,",
        "status change and timing on this ticket as yours rather than the control plane's —",
        "without it the comparison this run exists for is impossible. It also takes this run",
        "**out of the queue entirely**: you do not wait for a lane, and you must not poll one.",
        "",
        "**2. Check out a stage.**",
        "",
        "```",
        "loregarden_begin_external_stage",
        '  run_id="<run_id from step 1>"',
        "```",
        "",
        "You get back `agent_run_id`, `stage_key`, `repo_path`, and `prompt`. That prompt is",
        "the exact instruction set Loregarden's own agent would receive for this stage —",
        "follow it, in `repo_path`, instead of improvising from the ticket text above. An",
        "empty `agent_run_id` means the stage runs no agent; `message` says why, and you stop.",
        "",
        "**3. Do the stage's work.**",
        "",
        "**4. Hand the stage back.**",
        "",
        "```",
        "loregarden_finish_external_stage",
        '  agent_run_id="<from step 2>"',
        '  transcript="<your stage report, verbatim>"',
        "```",
        "",
        "`transcript` must contain the `<<<LOREGARDEN_STAGE_REPORT>>>` block the stage prompt",
        "asks for, unedited. Loregarden parses it and routes the workflow exactly as it does",
        "for its own runs — a rejected report reroutes upstream, a blocked one blocks the",
        "ticket. The reply tells you the stage's `duration_seconds`, the next stage, and",
        "whether the workflow is finished.",
        "",
        "**5. Repeat 2–4** until the reply says `workflow_finished`, or you are blocked.",
        "",
        "**6. Close the run.**",
        "",
        "```",
        "loregarden_complete_orchestration",
        '  run_id="<run_id from step 1>"',
        '  status="succeeded"   # or failed / blocked',
        "```",
        "",
        "This is what stamps the ticket's finish time. Do not skip it: an orchestration left",
        "open has no completion time, and a run with no completion time cannot be compared",
        "against anything.",
        "",
        "## Rules",
        "",
        "- Do not call `loregarden_start_stage` or `loregarden_complete_stage` directly. The",
        "  two `external_*` tools wrap them and also open, time and settle the agent run —",
        "  calling the raw pair leaves the stage with no run behind it.",
        "- Do not start a second orchestration for this ticket while one is open.",
        "- Timing is measured server-side, from these calls. Do not report your own elapsed",
        "  time; make the calls at the moments they describe and the numbers follow.",
        "- Record assumptions with `loregarden_append_checkpoint` rather than stopping to ask,",
        "  and call `loregarden_block_ticket` if the work is genuinely blocked.",
        "- Never write a report, summary or findings `.md` file. Long output goes to",
        "  `loregarden_attach_artifact`.",
    ]
    return ExternalHarnessPromptView(
        harness=harness,
        ticket_id=ticket.id,
        external_id=ticket.external_id,
        workspace_slug=workspace.slug,
        prompt="\n".join(lines),
    )
