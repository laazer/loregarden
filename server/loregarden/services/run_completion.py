"""Settling a finished agent run: stage advance, artifacts, and failure recovery.

Split out of ``OrchestrationService``, which was at its size cap. The seam is real
rather than cosmetic — this is the whole "a run just ended, now what" path, and it
has one property worth stating in one place: it runs *after* the run's terminal
status is already committed. Everything here is therefore best-effort with respect
to that status, and its failure modes are about not stranding the ticket.

``OrchestrationService.complete_run`` remains the entry point; these take the
service so they can reach the workflow resolution that still lives on it.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from loregarden.core.event_bus import event_bus
from loregarden.core.workflow_loader import stage_display_name
from loregarden.models.domain import (
    AgentRun,
    Approval,
    Artifact,
    EventType,
    ReworkStopReason,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.artifact_service import (
    record_blocking_issue,
    refresh_execution_artifacts,
)
from loregarden.services.rework_feedback import (
    record_reroute_exhausts_budget,
    rework_reroute_count,
)
from loregarden.services.run_log_stream import finalize_run_log_artifact
from loregarden.services.stage_report import (
    StageReport,
    parse_stage_report,
    stage_report_artifact_content,
)
from loregarden.services.ticket_state_service import choose
from loregarden.services.usage_limits import (
    UsageLimit,
    detect_usage_limit,
    format_usage_limit_hint,
    usage_limit_blocking_issue,
)
from loregarden.services.workflow_routing import apply_stage_route, previous_stage_key
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, select

if TYPE_CHECKING:
    from loregarden.services.orchestration import OrchestrationService

logger = logging.getLogger(__name__)


def _stage_report_artifact(stage_key: str, report: StageReport) -> dict:
    """Build a `context`-kind artifact payload from a parsed stage report."""
    return {
        "kind": "context",
        "title": f"Stage report — {stage_key}",
        "content": stage_report_artifact_content(stage_key, report),
    }


def _blocking_issue(session: Session, ticket: Ticket, run: AgentRun, message: str) -> str:
    return record_blocking_issue(
        session, ticket, run_id=run.id, stage_key=run.stage_key, message=message
    )


def _rework_target(stages, from_key: str, reroute_to_stage: str) -> str:
    """The stage this rework will re-run, for keying the rework-feedback ledger.

    Mirrors ``apply_stage_route``'s reject resolution for the common cases: an
    explicit, valid ``reroute_to_stage`` wins; otherwise it falls back to the
    immediately preceding stage. A template ``reject`` route to a non-adjacent
    stage isn't modelled here, so the ledger key can differ by one in that rare
    case — the feedback is still recorded, just under the fallback key.
    """
    keys = {stage.key for stage in stages}
    if reroute_to_stage and reroute_to_stage in keys:
        return reroute_to_stage
    return previous_stage_key(stages, from_key) or ""


def _reroute_or_block_for_rework(
    orch: OrchestrationService,
    ticket: Ticket,
    run: AgentRun,
    report: StageReport,
    instance,
    stages,
    stderr: str,
) -> None:
    """An agent reported fail/needs_rework. Record the full feedback for the
    re-run agent (in the durable ledger, not the truncated blocking_issues), then
    either reroute upstream or — if this target has already been rerouted to the
    loop cap — block for a human instead of bouncing the work yet again.
    """
    full_context = report.reroute_context or stderr[:2000] or "Agent run failed"
    target_stage = _rework_target(stages, run.stage_key, report.reroute_to_stage or "")
    exhausted = record_reroute_exhausts_budget(
        orch.session,
        ticket,
        target_stage=target_stage,
        from_stage=run.stage_key,
        context=full_context,
        run_id=run.id,
    )
    if exhausted is not ReworkStopReason.NONE:
        count = rework_reroute_count(orch.session, ticket, target_stage)
        if exhausted is ReworkStopReason.STUCK:
            reason = (
                f"Rework loop is not converging: '{target_stage}' raised the same finding "
                f"against the same commit twice running (round {count}). Re-running cannot "
                f"differ from the last attempt — nothing changed in the request or the code "
                f"it was about. Paused for a human; the repeated finding is in the rework "
                f"feedback."
            )
        else:
            reason = (
                f"Rework loop: '{target_stage}' has been rerouted {count}× without passing "
                f"'{run.stage_key}'. Paused for a human — see the accumulated rework feedback "
                f"before re-running."
            )
        ticket.blocking_issues = _blocking_issue(orch.session, ticket, run, reason)
        set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
        choose(orch.session, ticket, TicketState.BLOCKED, actor="orchestrator", emit=False)
        return

    try:
        apply_stage_route(
            ticket,
            instance,
            stages,
            orch._resolve_transitions(ticket),
            from_key=run.stage_key,
            outcome="reject",
            next_stage_key=report.reroute_to_stage or "",
            blocking_issues=_blocking_issue(orch.session, ticket, run, full_context),
        )
    except ValueError:
        # No reject transition, no agent-specified target, and no preceding
        # stage to fall back to (already first-in-order).
        ticket.blocking_issues = _blocking_issue(orch.session, ticket, run, full_context)
        set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)


def complete_run_tail(
    orch: OrchestrationService,
    run: AgentRun,
    *,
    status: RunStatus,
    stdout: str,
    stderr: str,
    artifacts: list[dict] | None,
    advance_workflow: bool,
) -> AgentRun:
    """The ticket-dependent half of ``complete_run``. See that caller for why it splits."""
    ticket = orch.get_ticket(run.ticket_id)
    if not ticket:
        return run

    report = parse_stage_report(stdout)
    if advance_workflow:
        advance_stage_after_run(orch, ticket, run, report, status, stderr, stdout=stdout)

    persist_run_artifacts(orch, ticket, run, status, stderr, report, artifacts, stdout=stdout)

    event_bus.publish(
        orch.session,
        EventType.AGENT_RUN_COMPLETED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_id=run.id,
        payload={"status": status.value},
    )
    if advance_workflow:
        workspace = orch.session.get(Workspace, ticket.workspace_id)
        if workspace:
            refresh_execution_artifacts(
                orch.session,
                ticket=ticket,
                run=run,
                workspace=workspace,
            )
    finalize_run_log_artifact(run, status=status, stderr=stderr)
    return run


def release_execution_slot(orch: OrchestrationService, run: AgentRun) -> None:
    """Give back the queue slot this run held, if it held one.

    Called from `complete_run`'s `finally` rather than from the tail above,
    because everything in that tail is best-effort and any of it can raise: a
    ticket that will not load returns early, an artifact refresh that fails
    unwinds the rest. Releasing last *inside* the tail meant each of those
    leaked a slot — the board then showed the lane running a run that had
    finished, which is where a lane reading "succeeded" comes from.

    Hooked at completion because this is the one place every run reaches its
    terminal status, whatever started it. `ParallelRunService.on_parallel_run_complete`
    was written to do this and had no callers at all, so a run started from the
    queue claimed a slot and never gave it back — the board lost a lane per
    launch until nothing could start.

    Best-effort: a run's completion is already durable by the time we get here,
    and failing to release a slot must not undo it. The slot is recoverable
    (it releases on the next completion, or by hand); a lost completion is not.
    """
    from loregarden.services.parallel_queue import ParallelQueueService

    try:
        ParallelQueueService(orch.session).on_run_complete_sync(run.id)
    except Exception:
        logger.warning("Failed to release the execution slot for run %s", run.id, exc_info=True)


def settle_stage_after_failed_completion(
    orch: OrchestrationService, run: AgentRun, exc: Exception
) -> None:
    """Last-ditch attempt to leave the stage in a state an operator can act on.

    Most failures in the tail are not the ticket's fault — a routing error, a bad
    artifact write — and the ticket loads fine, so the stage can be blocked here and
    shows up in the workflow pane immediately. When the ticket itself is unreadable
    this cannot work by definition; the stage stays RUNNING and
    ``settle_stranded_stages`` picks it up on the next start.
    """
    try:
        ticket = orch.get_ticket(run.ticket_id)
        instance, stages = orch._resolve_stages(ticket) if ticket else (None, None)
        if not ticket or not instance or not stages or not run.stage_key:
            return
        set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
        ticket.blocking_issues = _blocking_issue(
            orch.session, ticket, run, f"Run completion failed: {exc}"
        )
        orch.session.add(ticket)
        orch.session.add(instance)
        orch.session.commit()
    except Exception:
        orch.session.rollback()
        logger.exception(
            "Could not settle stage %r for ticket %s; leaving it for the reaper",
            run.stage_key,
            run.ticket_id,
        )


_MISSING_STAGE_REPORT = (
    "Agent run exited successfully but emitted no parseable "
    "<<<LOREGARDEN_STAGE_REPORT>>> block. Stage outcome is unknown — "
    "re-run and emit pass|fail|needs_rework|blocked before this stage can advance."
)


def run_usage_limit(status: RunStatus, stdout: str, stderr: str) -> UsageLimit | None:
    """The provider limit that ended this run, or None.

    stderr is trusted on any non-success: nothing an agent writes reaches it, so a
    limit phrase there came from the CLI. stdout is the agent's own transcript —
    an agent working on quota handling will say "usage limit" in prose — so it is
    only read when the CLI also stated a reset window, which prose does not.
    """
    if status == RunStatus.CANCELLED:
        return None
    if status != RunStatus.SUCCEEDED:
        from_stderr = detect_usage_limit(stderr)
        if from_stderr:
            return from_stderr
    limit = detect_usage_limit(stdout)
    if limit and limit.reset_text:
        return limit
    return None


def _block_for_usage_limit(
    orch: OrchestrationService,
    ticket: Ticket,
    run: AgentRun,
    instance,
    stages,
    status: RunStatus,
    limit: UsageLimit,
) -> None:
    """Block on the provider's limit rather than on a symptom of it.

    Same stage disposition as the failure branches this displaces — only the
    operator-facing reason changes, from a raw provider dump (or the thoroughly
    misleading "emitted no stage report") to the actual cause. The ticket itself
    is halted only on the clean-exit path, matching the fail-closed branch a
    quota-killed exit-0 run used to land in.
    """
    ticket.blocking_issues = _blocking_issue(
        orch.session, ticket, run, usage_limit_blocking_issue(limit)
    )
    set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
    if status == RunStatus.SUCCEEDED:
        choose(orch.session, ticket, TicketState.BLOCKED, actor="orchestrator", emit=False)


def _advance_clean_exit(
    orch: OrchestrationService,
    ticket: Ticket,
    run: AgentRun,
    report,
    instance,
    stages,
) -> Approval | None:
    """Settle a run whose CLI exited 0, returning any gate approval it created."""
    if report is None:
        # Fail closed: a clean CLI exit without a stage report used to advance
        # as pass (exit-code-only fallback). Gatekeepers that rejected in prose
        # but never emitted the sentinel — or whose MCP complete_stage call was
        # cancelled — then silently promoted bad work. Require an explicit
        # report before any agent stage can leave RUNNING.
        ticket.blocking_issues = _blocking_issue(orch.session, ticket, run, _MISSING_STAGE_REPORT)
        set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
        choose(orch.session, ticket, TicketState.BLOCKED, actor="orchestrator", emit=False)
        return None

    gate_approval: Approval | None = None
    stage_status = StageStatus.DONE
    stage_def = next((s for s in stages if s.key == run.stage_key), None)
    if stage_def and stage_def.gate_required:
        stage_status = StageStatus.AWAITING
        template = orch.get_template_for_ticket(ticket)
        if template:
            stage_name = stage_display_name(template, run.stage_key)
            gate_approval = orch._create_workflow_gate_approval(
                ticket, run.stage_key, stage_name, stage_def=stage_def
            )
    set_stage_status(ticket, instance, stages, run.stage_key, stage_status)
    ticket.blocking_issues = ""
    return gate_approval


def advance_stage_after_run(
    orch: OrchestrationService,
    ticket: Ticket,
    run: AgentRun,
    report,
    status: RunStatus,
    stderr: str,
    *,
    stdout: str = "",
) -> None:
    instance, stages = orch._resolve_stages(ticket)
    if not instance or not stages:
        return

    # Only when the agent produced no outcome of its own: a run that emitted a
    # stage report reached a verdict, and that verdict outranks anything the
    # provider printed on the way out.
    limit = run_usage_limit(status, stdout, stderr) if report is None else None

    gate_approval: Approval | None = None
    if limit is not None:
        _block_for_usage_limit(orch, ticket, run, instance, stages, status, limit)
    elif report and report.status == "blocked":
        # Distinct from fail/needs_rework: the agent isn't reporting bad work to
        # redo upstream, it's reporting it cannot proceed at all (e.g. needs a
        # human decision) — reroute-for-rework would just waste a cycle, so this
        # halts the ticket directly instead.
        fallback = "Agent reported this stage as blocked"
        message = report.reroute_context or stderr[:2000] or fallback
        ticket.blocking_issues = _blocking_issue(orch.session, ticket, run, message)
        set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
    elif report and report.status in ("fail", "needs_rework"):
        _reroute_or_block_for_rework(orch, ticket, run, report, instance, stages, stderr)
    elif status == RunStatus.SUCCEEDED:
        gate_approval = _advance_clean_exit(orch, ticket, run, report, instance, stages)
    elif status == RunStatus.CANCELLED:
        # A stop is not a failure — leave the stage re-runnable with no inbox noise.
        ticket.blocking_issues = ""
        set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.PENDING)
    else:
        ticket.blocking_issues = _blocking_issue(
            orch.session, ticket, run, stderr[:2000] or "Agent run failed"
        )
        set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
    orch.session.add(ticket)
    orch.session.add(instance)
    orch.session.commit()

    # A gate_required stage reached under auto_approve resolves itself
    # immediately instead of parking at AWAITING for a human — the
    # approval row is still created above (audit trail), just pre-resolved.
    # Delegated because ApprovalService lives in the orchestration module, and
    # importing it here would close a cycle.
    if gate_approval is not None and run.auto_approve:
        orch.auto_resolve_gate_approval(gate_approval, run)
        orch.session.refresh(ticket)


def persist_run_artifacts(
    orch: OrchestrationService,
    ticket: Ticket,
    run: AgentRun,
    status: RunStatus,
    stderr: str,
    report,
    artifacts: list[dict] | None,
    *,
    stdout: str = "",
) -> None:
    artifacts = list(artifacts or [])
    if status not in (RunStatus.SUCCEEDED, RunStatus.CANCELLED):
        limit = run_usage_limit(status, stdout, stderr) if report is None else None
        message = format_usage_limit_hint(limit) if limit else ""
        artifacts.append(
            {
                "kind": "error",
                "title": (
                    f"Run {run.run_code} — usage limit" if limit else f"Run {run.run_code} failed"
                ),
                "content": {
                    "message": message
                    or stderr[:4000]
                    or ticket.blocking_issues
                    or "Agent run failed",
                    "run_code": run.run_code,
                    "agent_id": run.agent_id,
                    "stage_key": run.stage_key,
                    "command": run.command or "",
                },
            }
        )
    if report:
        artifacts.append(_stage_report_artifact(run.stage_key, report))

    for item in artifacts:
        if item.get("kind") == "log":
            existing = orch.session.exec(
                select(Artifact).where(
                    Artifact.run_id == run.id,
                    Artifact.kind == "log",
                )
            ).first()
            if existing:
                continue
        artifact = Artifact(
            ticket_id=ticket.id,
            run_id=run.id,
            kind=item.get("kind", "log"),
            title=item.get("title", ""),
            content_json=json.dumps(item.get("content", {})),
        )
        orch.session.add(artifact)
        orch.session.commit()
        event_bus.publish(
            orch.session,
            EventType.ARTIFACT_CREATED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            run_id=run.id,
            artifact_id=artifact.id,
            payload={"kind": artifact.kind},
        )
