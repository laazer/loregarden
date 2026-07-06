from __future__ import annotations

import logging
import os
import threading

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.db.session import engine
from loregarden.models.domain import (
    AgentRun,
    OrchestrationDriver,
    OrchestrationRun,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

INTERRUPTED_RUN_MESSAGE = (
    "Agent run interrupted before completion (server reload or worker stopped). "
    "Re-run the stage to continue."
)


def fail_interrupted_runs(
    session: Session,
    *,
    ticket_id: str | None = None,
    stage_key: str | None = None,
    exclude_run_id: str | None = None,
    message: str = INTERRUPTED_RUN_MESSAGE,
) -> list[AgentRun]:
    """Mark orphaned in-flight runs as failed so stages do not stay stuck running."""
    query = select(AgentRun).where(
        col(AgentRun.status).in_([RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION])
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

    def start_run_async(self, ticket: Ticket, *, stage_key: str | None = None) -> AgentRun:
        """Create a run and mark the stage running; CLI executes in a background task."""
        target_key = stage_key or ticket.workflow_stage_key
        fail_interrupted_runs(
            self.session,
            ticket_id=ticket.id,
            stage_key=target_key or None,
        )
        run = self.orchestration.start_run(ticket, stage_key=stage_key)
        self.session.refresh(ticket)
        return run

    def start_stage_execution(
        self, ticket: Ticket, *, stage_key: str | None = None
    ) -> AgentRun | None:
        """Start an agent CLI run, or enter a human approval gate for agentless stages."""
        from loregarden.services.studio_service import is_agentless_stage

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

        return self.start_run_async(ticket, stage_key=stage_key)

    def list_runs(
        self,
        *,
        ticket_id: str | None = None,
        limit: int = 50,
    ) -> list[AgentRun]:
        query = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
        if ticket_id:
            query = query.where(AgentRun.ticket_id == ticket_id)
        return list(self.session.exec(query).all())

    def get_run(self, run_id: str) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)
