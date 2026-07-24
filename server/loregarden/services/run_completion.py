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
    RunStatus,
    StageStatus,
    Ticket,
    Workspace,
)
from loregarden.services.artifact_service import (
    record_blocking_issue,
    refresh_execution_artifacts,
)
from loregarden.services.run_log_stream import finalize_run_log_artifact
from loregarden.services.stage_report import (
    StageReport,
    parse_stage_report,
    stage_report_artifact_content,
)
from loregarden.services.workflow_routing import apply_stage_route
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
        advance_stage_after_run(orch, ticket, run, report, status, stderr)

    persist_run_artifacts(orch, ticket, run, status, stderr, report, artifacts)

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


def advance_stage_after_run(
    orch: OrchestrationService,
    ticket: Ticket,
    run: AgentRun,
    report,
    status: RunStatus,
    stderr: str,
) -> None:
    instance, stages = orch._resolve_stages(ticket)
    if not instance or not stages:
        return

    gate_approval: Approval | None = None
    if report and report.status == "blocked":
        # Distinct from fail/needs_rework: the agent isn't reporting bad work to
        # redo upstream, it's reporting it cannot proceed at all (e.g. needs a
        # human decision) — reroute-for-rework would just waste a cycle, so this
        # halts the ticket directly instead.
        fallback = "Agent reported this stage as blocked"
        message = report.reroute_context or stderr[:2000] or fallback
        ticket.blocking_issues = _blocking_issue(orch.session, ticket, run, message)
        set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
    elif report and report.status in ("fail", "needs_rework"):
        transitions = orch._resolve_transitions(ticket)
        try:
            apply_stage_route(
                ticket,
                instance,
                stages,
                transitions,
                from_key=run.stage_key,
                outcome="reject",
                next_stage_key=report.reroute_to_stage or "",
                blocking_issues=_blocking_issue(
                    orch.session, ticket, run, report.reroute_context or stderr[:2000]
                ),
            )
        except ValueError:
            # No reject transition, no agent-specified target, and no
            # preceding stage to fall back to (already first-in-order).
            ticket.blocking_issues = _blocking_issue(
                orch.session,
                ticket,
                run,
                report.reroute_context or stderr[:2000] or "Agent run failed",
            )
            set_stage_status(ticket, instance, stages, run.stage_key, StageStatus.BLOCKED)
    elif status == RunStatus.SUCCEEDED:
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
) -> None:
    artifacts = list(artifacts or [])
    if status != RunStatus.SUCCEEDED:
        artifacts.append(
            {
                "kind": "error",
                "title": f"Run {run.run_code} failed",
                "content": {
                    "message": stderr[:4000] or ticket.blocking_issues or "Agent run failed",
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
