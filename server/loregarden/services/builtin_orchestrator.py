"""Builtin orchestrator driver — top-level run invoking stage sub-agents via CLI."""

from __future__ import annotations

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session


class BuiltinOrchestrator:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.callbacks = OrchestrationCallbackService(session)
        self.orch = OrchestrationService(session)
        self.executor = CliAgentExecutor(session)

    def execute(
        self,
        ticket: Ticket,
        profile: OrchestrationProfile,
        *,
        max_stages: int | None = None,
    ) -> OrchestrationRun:
        limit = max_stages if max_stages is not None else profile.max_stages_per_run
        orch_run = self.callbacks.start_orchestration_run(
            ticket,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            profile_slug=profile.slug,
        )
        self.session.refresh(ticket)

        stages_run = 0
        try:
            while True:
                if ticket.state in (TicketState.BLOCKED, TicketState.DONE, TicketState.WONT_DO):
                    break

                instance, stages = self.orch._resolve_stages(ticket)
                if not instance or not stages:
                    break

                stage_map = parse_stage_map(instance, stages)
                target_key = self._next_executable_stage(ticket, stages, stage_map)
                if not target_key:
                    self.callbacks.complete_orchestration(
                        orch_run,
                        ticket,
                        status=OrchestrationRunStatus.SUCCEEDED,
                    )
                    self.session.refresh(orch_run)
                    return orch_run

                if limit > 0 and stages_run >= limit:
                    orch_run.error_message = f"Paused after {stages_run} stage(s)"
                    self.session.add(orch_run)
                    self.session.commit()
                    self.session.refresh(orch_run)
                    return orch_run

                stage_def = next(s for s in stages if s.key == target_key)
                agent_run = self.orch.start_run(
                    ticket,
                    stage_key=target_key,
                    orchestration_run_id=orch_run.id,
                )
                completed = self.executor.execute(agent_run, ticket)
                self.session.refresh(ticket)
                stages_run += 1

                if completed.status != RunStatus.SUCCEEDED:
                    self.callbacks.block_ticket(
                        orch_run,
                        ticket,
                        stage_key=target_key,
                        message=completed.stderr or "Stage sub-agent failed",
                    )
                    self.session.refresh(orch_run)
                    return orch_run

                self.session.refresh(ticket)
                instance, stages = self.orch._resolve_stages(ticket)
                stage_map = parse_stage_map(instance, stages) if instance else {}
                status_after = stage_map.get(target_key, ticket.workflow_stage_status)

                if status_after == StageStatus.AWAITING:
                    self.callbacks.complete_orchestration(
                        orch_run,
                        ticket,
                        status=OrchestrationRunStatus.SUCCEEDED,
                        message="Awaiting human approval",
                    )
                    self.session.refresh(orch_run)
                    return orch_run

                if not StateMachine.next_stage_key(stages, target_key):
                    self.callbacks.complete_orchestration(
                        orch_run,
                        ticket,
                        status=OrchestrationRunStatus.SUCCEEDED,
                    )
                    self.session.refresh(orch_run)
                    return orch_run

            final_status = (
                OrchestrationRunStatus.BLOCKED
                if ticket.state == TicketState.BLOCKED
                else OrchestrationRunStatus.SUCCEEDED
            )
            self.callbacks.complete_orchestration(orch_run, ticket, status=final_status)
        except Exception as exc:
            self.callbacks.block_ticket(
                orch_run,
                ticket,
                message=str(exc),
            )
        self.session.refresh(orch_run)
        return orch_run

    def _next_executable_stage(self, ticket: Ticket, stages, stage_map) -> str | None:
        ordered = sorted(stages, key=lambda s: s.order)
        keys = [s.key for s in ordered]

        for status in (StageStatus.RUNNING, StageStatus.AWAITING, StageStatus.BLOCKED):
            for key in keys:
                if stage_map.get(key) == status:
                    if status == StageStatus.BLOCKED:
                        return None
                    return key

        if ticket.workflow_stage_key and ticket.workflow_stage_key in stage_map:
            st = stage_map[ticket.workflow_stage_key]
            if st in (StageStatus.PENDING, StageStatus.RUNNING):
                return ticket.workflow_stage_key

        for key in keys:
            if stage_map.get(key) == StageStatus.PENDING:
                return key
        return None
