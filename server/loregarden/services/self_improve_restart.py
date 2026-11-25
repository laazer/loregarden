"""Detect when Loregarden can safely restart during self-improvement workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from loregarden.config import settings
from loregarden.mcp.shaped_errors import UnknownWorkspaceSlug
from loregarden.models.domain import (
    AgentRun,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    Workspace,
)
from loregarden.services.drain import begin_drain, end_drain, wait_for_quiescence
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_lease import run_has_renewer
from loregarden.services.run_service import fail_stale_handoff_runs
from loregarden.services.studio_routing import is_agentless_stage
from sqlmodel import Session, col, select

_ACTIVE_AGENT_STATUSES = frozenset({RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION})

# The dev-server supervisor (`loregarden.dev_supervisor`) does not watch Python files,
# so editing one does NOT reload it — touching this file is the only trigger. Without
# it an agent's backend fix sits in the working tree while the running process serves the
# old code, and the human testing at a gate sees the bug as unfixed.
RELOAD_SENTINEL = Path("server") / ".self-improve-restart"

# Reloading is only meaningful where the server IS the product under test.
SELF_IMPROVE_WORKSPACE = "loregarden"


class ReloadBlockedError(RuntimeError):
    """Raised when a reload would kill work that is currently in flight."""

    def __init__(self, blockers: list[str], detail: dict) -> None:
        super().__init__(", ".join(blockers) or "reload blocked")
        self.blockers = blockers
        self.detail = detail


def _human_gate_ticket_payload(
    session: Session,
    ticket: Ticket,
    orch: OrchestrationService,
) -> dict | None:
    if ticket.workflow_stage_status != StageStatus.AWAITING:
        return None

    _instance, stages = orch._resolve_stages(ticket)
    if not stages:
        return None

    stage_key = ticket.workflow_stage_key
    stage_def = next((stage for stage in stages if stage.key == stage_key), None)
    if not stage_def or not is_agentless_stage(stage_def):
        return None

    return {
        "ticket_id": ticket.id,
        "external_id": ticket.external_id,
        "title": ticket.title,
        "workflow_stage_key": stage_key,
        "workflow_stage_status": ticket.workflow_stage_status.value,
        "revision": ticket.revision,
        "restart_key": f"{ticket.id}:{stage_key}:{ticket.revision}",
    }


def _workspace_or_error(session: Session, workspace_slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
    if not workspace:
        raise UnknownWorkspaceSlug(workspace_slug)
    return workspace


def _cannot_yield(runs: list[AgentRun], orchestrations: list[OrchestrationRun]) -> list[str]:
    """In-flight work that holding new work back would not bring to a stop.

    A prompt waits on a person, and a terminal agent answers to its own session:
    either can outlast any hold (`reload_hold`).
    """
    reasons = [
        f"{run.run_code} waits for a person's approval"
        for run in runs
        if run.status is RunStatus.AWAITING_PERMISSION
    ]
    reasons += [
        f"{run.run_code} runs outside this server"
        for run in runs
        if not run_has_renewer(run) or run.handoff_accepted_at is not None
    ]
    reasons += [
        f"{run.run_code} is driven from outside this server ({run.driver.value})"
        for run in orchestrations
        if run.driver is not OrchestrationDriver.BUILTIN_AUTOPILOT
    ]
    return reasons


def _in_flight_state(session: Session) -> dict:
    """Work a restart would kill. Shared by the watcher's poll and the manual reload.

    Counted in every workspace, not only the one being reloaded: there is one
    server process, and the builtin driver of a loremaker ticket dies with it as
    surely as loregarden's own. Scoping this to the reloaded workspace is how a
    follow-main reload onto #941 killed lmkr-execution-leaves-11's driver
    mid-implement, and its orchestration lapsed half an hour later.
    """
    # A dead terminal-handoff run is not work a restart would kill — and left
    # unreaped it deadlocks the watcher: the restart that would recover it is
    # blocked by it. The watcher polls this continuously, so reaping here keeps
    # phantom handoff runs from wedging the whole self-improve loop.
    fail_stale_handoff_runs(session)
    active_agent_runs = list(
        session.exec(
            select(AgentRun).where(
                col(AgentRun.status).in_(_ACTIVE_AGENT_STATUSES),
            )
        ).all()
    )
    active_orchestrations = list(
        session.exec(
            select(OrchestrationRun).where(
                OrchestrationRun.status == OrchestrationRunStatus.RUNNING,
            )
        ).all()
    )
    running_workflow_tickets = list(
        session.exec(
            select(Ticket).where(Ticket.workflow_stage_status == StageStatus.RUNNING)
        ).all()
    )

    blockers: list[str] = []
    if active_agent_runs:
        blockers.append("active_agent_runs")
    if active_orchestrations:
        blockers.append("active_orchestrations")
    if running_workflow_tickets:
        blockers.append("running_workflow_stages")

    return {
        "blockers": blockers,
        "cannot_yield": _cannot_yield(active_agent_runs, active_orchestrations),
        "active_agent_runs": [
            {
                "id": run.id,
                "run_code": run.run_code,
                "ticket_id": run.ticket_id,
                "stage_key": run.stage_key,
                "status": run.status.value,
            }
            for run in active_agent_runs
        ],
        "active_orchestrations": [
            {
                "id": run.id,
                "run_code": run.run_code,
                "ticket_id": run.ticket_id,
                "status": run.status.value,
            }
            for run in active_orchestrations
        ],
        "running_workflow_tickets": [
            {
                "ticket_id": ticket.id,
                "external_id": ticket.external_id,
                "workflow_stage_key": ticket.workflow_stage_key,
            }
            for ticket in running_workflow_tickets
        ],
    }


def evaluate_self_improve_restart(
    session: Session,
    *,
    workspace_slug: str = SELF_IMPROVE_WORKSPACE,
) -> dict:
    """Return whether the dev server may restart for a human-triage handoff."""
    workspace = _workspace_or_error(session, workspace_slug)

    orch = OrchestrationService(session)
    tickets = list(session.exec(select(Ticket).where(Ticket.workspace_id == workspace.id)).all())

    human_gate_tickets = [
        payload
        for ticket in tickets
        if (payload := _human_gate_ticket_payload(session, ticket, orch)) is not None
    ]

    state = _in_flight_state(session)
    blockers = list(state["blockers"])
    if not human_gate_tickets:
        # Only the unattended watcher needs a ticket parked at a human gate; a person
        # clicking "bring in changes" is the handoff.
        blockers.insert(0, "no_ticket_at_human_triage")

    restart_key = human_gate_tickets[0]["restart_key"] if human_gate_tickets else ""

    return {
        "workspace_slug": workspace_slug,
        "ready": not blockers,
        "restart_key": restart_key,
        "blockers": blockers,
        "human_gate_tickets": human_gate_tickets,
        "active_agent_runs": state["active_agent_runs"],
        "active_orchestrations": state["active_orchestrations"],
        "running_workflow_tickets": state["running_workflow_tickets"],
    }


def evaluate_reload_readiness(
    session: Session,
    *,
    workspace_slug: str = SELF_IMPROVE_WORKSPACE,
) -> dict:
    """Whether a human can pull working-tree changes into the running server now."""
    _workspace_or_error(session, workspace_slug)
    state = _in_flight_state(session)
    return {
        "workspace_slug": workspace_slug,
        "supported": workspace_slug == SELF_IMPROVE_WORKSPACE,
        "ready": not state["blockers"],
        **state,
    }


def trigger_reload(session: Session, *, workspace_slug: str = SELF_IMPROVE_WORKSPACE) -> dict:
    """Touch the reload sentinel so the dev server restarts onto the current tree.

    Refuses while work is in flight: a restart kills the agent subprocess and orphans
    its run, which is how a stage's work gets lost.
    """
    readiness = evaluate_reload_readiness(session, workspace_slug=workspace_slug)
    if not readiness["supported"]:
        raise ValueError(
            f"Reloading is only supported for the {SELF_IMPROVE_WORKSPACE} workspace "
            f"(the server under test); got {workspace_slug!r}"
        )
    if readiness["blockers"]:
        # Drain first, then decide. This used to refuse outright, which made the
        # reload path a second mechanism sitting beside shutdown rather than a
        # policy on top of it: a stage two seconds from finishing blocked a
        # reload for as long as it ran. Now the same bounded wait every shutdown
        # takes runs here too, and the refusal is what happens when that window
        # closes with work still in flight.
        begin_drain()
        try:
            report = wait_for_quiescence(timeout_seconds=settings.drain_timeout_seconds)
            if not report.clean:
                readiness = evaluate_reload_readiness(session, workspace_slug=workspace_slug)
                raise ReloadBlockedError(readiness["blockers"], readiness)
        except ReloadBlockedError:
            # The process is staying up, so it must stop refusing new work.
            end_drain()
            raise
        # Cleared: the sentinel write below restarts us, and the new process
        # starts undrained. Leaving the flag set would be inherited by nothing,
        # but a reload that fails to fire would leave this one refusing forever.
        end_drain()

    return touch_reload_sentinel()


def touch_reload_sentinel() -> dict:
    """Write the sentinel the dev-server supervisor watches; the caller checked readiness."""
    sentinel = settings.repo_root / RELOAD_SENTINEL
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sentinel.write_text(f"{stamp}\n", encoding="utf-8")
    return {"triggered": True, "at": stamp, "sentinel": str(sentinel)}
