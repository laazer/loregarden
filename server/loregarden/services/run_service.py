from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.db.session import engine
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
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

INTERRUPTED_RUN_MESSAGE = (
    "Agent run interrupted before completion (server reload or worker stopped). "
    "Re-run the stage to continue."
)

TERMINAL_HANDOFF_COMMAND_PREFIX = "[terminal-handoff]"

STALE_HANDOFF_NEVER_STARTED_MESSAGE = (
    "Terminal handoff was never started — the copied command did not check in. "
    "Generate a fresh handoff or re-run the stage to continue."
)

STALE_HANDOFF_SHELL_DIED_MESSAGE = (
    "Terminal handoff shell exited without completing the stage. Re-run the stage to continue."
)

HANDOFF_EXITED_MESSAGE = (
    "Terminal handoff CLI session ended without completing the stage. Re-run the stage to continue."
)


def _handoff_checkin_grace_seconds() -> int:
    raw = os.environ.get("LOREGARDEN_HANDOFF_CHECKIN_GRACE_SECONDS", "")
    return int(raw) if raw.isdigit() else 900


def _pid_alive(pid: int) -> bool:
    """Whether `pid` is a live process on this host.

    Valid only because terminal handoffs are pasted into a shell on the same
    machine as this control plane — there is no remote-execution path.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def settle_dead_handoff_run(
    session: Session, run: AgentRun, *, message: str, session_ran: bool = True
) -> None:
    """Settle a handoff run whose terminal session is gone.

    Nothing settles a handoff AgentRun while its terminal works — the session
    advances the *workflow* through MCP/UI and the run row just sits RUNNING. So
    the workflow's own state is the only record of what the session achieved:

    - stage still RUNNING → the dead session was the active work; fail the run
      and let the advance mark the stage BLOCKED for a re-run.
    - stage progressed (moved to another key, DONE, or AWAITING a gate) and a
      session actually ran (``session_ran``) → it did its job before ending; the
      run succeeded, and re-touching the workflow here would corrupt real state
      (e.g. re-BLOCK an AWAITING gate). A never-started handoff earns no such
      credit — a human advancing the stage by hand does not make the run a
      success — but the workflow is still left alone.
    - anything else (already BLOCKED, ticket gone) → fail the run, leave the
      workflow alone.
    """
    ticket = session.get(Ticket, run.ticket_id)
    stage_still_running = bool(
        ticket
        and ticket.workflow_stage_key == run.stage_key
        and ticket.workflow_stage_status == StageStatus.RUNNING
    )
    progressed = session_ran and bool(
        ticket
        and (
            ticket.workflow_stage_key != run.stage_key
            or ticket.workflow_stage_status in (StageStatus.DONE, StageStatus.AWAITING)
        )
    )
    OrchestrationService(session).complete_run(
        run,
        status=RunStatus.SUCCEEDED if progressed else RunStatus.FAILED,
        stderr="" if progressed else message,
        advance_workflow=stage_still_running,
    )


def fail_stale_handoff_runs(session: Session, *, ticket_id: str | None = None) -> list[AgentRun]:
    """Reap terminal-handoff runs that are provably dead.

    A handoff run is created RUNNING before any process exists (see
    ``prepare_terminal_handoff``), so an in-flight status alone proves nothing.
    Two conditions are decisive: the command never checked in within the grace
    period (it was never pasted), or it checked in with a shell pid that is no
    longer alive (the terminal died). Anything else is treated as live.
    """
    query = select(AgentRun).where(
        col(AgentRun.status).in_([RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]),
        col(AgentRun.command).startswith(TERMINAL_HANDOFF_COMMAND_PREFIX),
    )
    if ticket_id:
        query = query.where(AgentRun.ticket_id == ticket_id)

    now = datetime.now(timezone.utc)
    grace = _handoff_checkin_grace_seconds()
    reaped: list[AgentRun] = []
    for run in session.exec(query).all():
        if run.handoff_accepted_at is None:
            started_at = run.started_at or run.created_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            if (now - started_at).total_seconds() < grace:
                continue
            settle_dead_handoff_run(
                session, run, message=STALE_HANDOFF_NEVER_STARTED_MESSAGE, session_ran=False
            )
        elif run.handoff_pid is not None and not _pid_alive(run.handoff_pid):
            settle_dead_handoff_run(session, run, message=STALE_HANDOFF_SHELL_DIED_MESSAGE)
        else:
            continue
        reaped.append(run)
    return reaped


def fail_interrupted_runs(
    session: Session,
    *,
    ticket_id: str | None = None,
    stage_key: str | None = None,
    exclude_run_id: str | None = None,
    message: str = INTERRUPTED_RUN_MESSAGE,
) -> list[AgentRun]:
    """Mark orphaned in-flight runs as failed so stages do not stay stuck running.

    Skips triage chat turns. They share the ``triage`` stage_key with the workflow
    stage but are a side channel: routing one through ``complete_run`` would advance
    the workflow off a chat message — blocking the ticket. ``agent_id`` is the
    discriminator (see ``triage_service.triage_run_status``); they are reconciled by
    ``triage_run_service.fail_interrupted_triage_turns`` instead.
    """
    query = select(AgentRun).where(
        col(AgentRun.status).in_([RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]),
        AgentRun.agent_id != TRIAGE_AGENT_ID,
    )
    if ticket_id:
        query = query.where(AgentRun.ticket_id == ticket_id)
    if stage_key:
        query = query.where(AgentRun.stage_key == stage_key)
    if exclude_run_id:
        query = query.where(AgentRun.id != exclude_run_id)

    orch = OrchestrationService(session)
    failed: list[AgentRun] = []
    for run in session.exec(query).all():
        orch.complete_run(run, status=RunStatus.FAILED, stderr=message)
        failed.append(run)
    return failed


def fail_interrupted_orchestration_runs(
    session: Session,
    *,
    ticket_id: str | None = None,
    message: str = INTERRUPTED_RUN_MESSAGE,
) -> list[OrchestrationRun]:
    """Mark orphaned orchestration runs as failed.

    An OrchestrationRun left at RUNNING after a server reload/crash makes
    start_orchestration_run() refuse all future runs for its ticket ("Orchestration
    already running"), even though nothing is actually running. fail_interrupted_runs
    already fails the orphaned AgentRun beneath it; this does the same for the parent.
    """
    query = select(OrchestrationRun).where(
        OrchestrationRun.status == OrchestrationRunStatus.RUNNING
    )
    if ticket_id:
        query = query.where(OrchestrationRun.ticket_id == ticket_id)

    callbacks = OrchestrationCallbackService(session)
    failed: list[OrchestrationRun] = []
    for run in session.exec(query).all():
        ticket = session.get(Ticket, run.ticket_id)
        if not ticket:
            continue
        callbacks.complete_orchestration(
            run, ticket, status=OrchestrationRunStatus.FAILED, message=message
        )
        failed.append(run)
    return failed


def execute_agent_run_background(run_id: str) -> None:
    """Run agent CLI with a fresh DB session."""
    try:
        with Session(engine) as session:
            run_svc = RunService(session)
            run = run_svc.get_run(run_id)
            if not run:
                logger.error("Background run not found: %s", run_id)
                return
            ticket = session.get(Ticket, run.ticket_id)
            if not ticket:
                logger.error("Background run ticket not found: %s", run_id)
                return
            run_svc.executor.execute(run, ticket)
    except Exception as exc:
        logger.exception("Background agent run failed: %s", run_id)
        try:
            with Session(engine) as session:
                run = session.get(AgentRun, run_id)
                if run and run.status in {RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION}:
                    OrchestrationService(session).complete_run(
                        run,
                        status=RunStatus.FAILED,
                        stderr=str(exc)[:2000] or "Background agent run failed",
                    )
        except Exception:
            logger.exception("Failed to mark run %s as failed after background error", run_id)


def schedule_agent_run(run_id: str) -> None:
    """Queue CLI execution without blocking the API event loop."""
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        execute_agent_run_background(run_id)
        return
    thread = threading.Thread(
        target=execute_agent_run_background,
        args=(run_id,),
        name=f"loregarden-run-{run_id[:8]}",
        daemon=True,
    )
    thread.start()


def execute_orchestration_background(
    ticket_id: str,
    *,
    max_stages: int | None = None,
    driver=None,
    stop_at_stage_key: str | None = None,
    auto_approve: bool = False,
) -> None:
    try:
        with Session(engine) as session:
            ticket = session.get(Ticket, ticket_id)
            if not ticket:
                logger.error("Background orchestration ticket not found: %s", ticket_id)
                return
            RunService(session).orchestrate_ticket(
                ticket,
                max_stages=max_stages,
                driver=driver,
                stop_at_stage_key=stop_at_stage_key,
                auto_approve=auto_approve,
            )
    except Exception as exc:
        logger.exception("Background orchestration failed for ticket %s: %s", ticket_id, exc)


def schedule_orchestration(
    ticket_id: str,
    *,
    max_stages: int | None = None,
    driver=None,
    stop_at_stage_key: str | None = None,
    auto_approve: bool = False,
) -> None:
    """Queue orchestration without blocking the API event loop."""
    if os.environ.get("LOREGARDEN_SYNC_ORCHESTRATION") == "1":
        execute_orchestration_background(
            ticket_id,
            max_stages=max_stages,
            driver=driver,
            stop_at_stage_key=stop_at_stage_key,
            auto_approve=auto_approve,
        )
        return
    thread = threading.Thread(
        target=execute_orchestration_background,
        args=(ticket_id,),
        kwargs={
            "max_stages": max_stages,
            "driver": driver,
            "stop_at_stage_key": stop_at_stage_key,
            "auto_approve": auto_approve,
        },
        name=f"loregarden-orch-{ticket_id[:8]}",
        daemon=True,
    )
    thread.start()


class RunService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.orchestration = OrchestrationService(session)
        self.executor = CliAgentExecutor(session)

    def orchestrate_ticket(
        self,
        ticket: Ticket,
        *,
        driver=None,
        max_stages: int | None = None,
        stop_at_stage_key: str | None = None,
        auto_approve: bool = False,
    ) -> OrchestrationRun:
        ws = self.session.get(Workspace, ticket.workspace_id)
        if not ws:
            raise ValueError("Ticket workspace not found")
        profile = resolve_orchestration_profile(ws)
        chosen = driver or profile.driver

        if chosen == OrchestrationDriver.BUILTIN_AUTOPILOT:
            return BuiltinOrchestrator(self.session).execute(
                ticket,
                profile,
                max_stages=max_stages,
                stop_at_stage_key=stop_at_stage_key,
                auto_approve=auto_approve,
            )
        if chosen == OrchestrationDriver.EXTERNAL_MCP:
            return OrchestrationCallbackService(self.session).start_orchestration_run(
                ticket,
                driver=chosen,
                profile_slug=profile.slug,
            )
        raise ValueError("manual_stage driver uses POST /start with manual=true")

    def start_and_execute(
        self, ticket: Ticket, *, stage_key: str | None = None
    ) -> tuple[AgentRun, Ticket]:
        run = self.orchestration.start_run(ticket, stage_key=stage_key)
        self.session.refresh(ticket)
        completed_run = self.executor.execute(run, ticket)
        self.session.refresh(ticket)
        return completed_run, ticket

    def start_run_async(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
        auto_approve: bool = False,
        timeout_seconds: int | None = None,
    ) -> AgentRun:
        """Create a run and mark the stage running; CLI executes in a background task."""
        target_key = stage_key or ticket.workflow_stage_key
        fail_interrupted_runs(
            self.session,
            ticket_id=ticket.id,
            stage_key=target_key or None,
        )
        run = self.orchestration.start_run(
            ticket,
            stage_key=stage_key,
            auto_approve=auto_approve,
            timeout_override_seconds=timeout_seconds,
        )
        self.session.refresh(ticket)
        return run

    def start_stage_execution(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
        auto_approve: bool = False,
        timeout_seconds: int | None = None,
    ) -> AgentRun | None:
        """Start an agent CLI run, or enter a human approval gate for agentless stages."""
        from loregarden.services.studio_routing import is_agentless_stage

        template = self.orchestration.get_template_for_ticket(ticket)
        if not template:
            raise ValueError("No workflow template for ticket workspace")

        _, stages = self.orchestration._resolve_stages(ticket)
        if not stages:
            raise ValueError("Ticket has no workflow instance")

        target_key = stage_key or ticket.workflow_stage_key
        if not target_key:
            raise ValueError("No stage selected")

        stage_def = next((s for s in stages if s.key == target_key), None)
        if not stage_def:
            raise ValueError(f"Unknown stage key: {target_key}")

        if is_agentless_stage(stage_def):
            if stage_def.key == "done":
                self.orchestration.finalize_workflow(ticket)
                self.session.refresh(ticket)
                return None
            self.orchestration.enter_human_gate(ticket, stage_key=target_key)
            self.session.refresh(ticket)
            return None

        return self.start_run_async(
            ticket, stage_key=stage_key, auto_approve=auto_approve, timeout_seconds=timeout_seconds
        )

    def list_runs(
        self,
        *,
        ticket_id: str | None = None,
        limit: int = 50,
        include_triage: bool = False,
    ) -> list[AgentRun]:
        query = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
        if ticket_id:
            query = query.where(AgentRun.ticket_id == ticket_id)
        if not include_triage:
            query = query.where(AgentRun.agent_id != TRIAGE_AGENT_ID)
        return list(self.session.exec(query).all())

    def get_run(self, run_id: str) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)
