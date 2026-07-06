"""Builtin orchestrator driver — top-level run invoking stage sub-agents via CLI."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.core.state_machine import StateMachine
from loregarden.db.session import engine
from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    AgentRun,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowStageDef,
    WorkItemType,
    Workspace,
)
from loregarden.services.gate_runner import run_transition_gates
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.studio_service import is_agentless_stage
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, select


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
        stop_at_stage_key: str | None = None,
        auto_approve: bool = False,
    ) -> OrchestrationRun:
        limit = max_stages if max_stages is not None else profile.max_stages_per_run
        orch_run = self.callbacks.start_orchestration_run(
            ticket,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            profile_slug=profile.slug,
            auto_approve=auto_approve,
            stop_at_stage_key=stop_at_stage_key or "",
        )
        self.session.refresh(ticket)

        stages_run = 0
        try:
            while True:
                if ticket.state in (TicketState.BLOCKED, TicketState.DONE, TicketState.WONT_DO):
                    break

                child_pause = self._orchestrate_incomplete_children(ticket, profile)
                if child_pause:
                    self.callbacks.complete_orchestration(
                        orch_run,
                        ticket,
                        status=OrchestrationRunStatus.SUCCEEDED,
                        message=child_pause,
                    )
                    self.session.refresh(orch_run)
                    return orch_run

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
                    self.callbacks.complete_orchestration(
                        orch_run,
                        ticket,
                        status=OrchestrationRunStatus.SUCCEEDED,
                        message=f"Paused after {stages_run} stage(s)",
                    )
                    self.session.refresh(orch_run)
                    return orch_run

                stage_def = next(s for s in stages if s.key == target_key)
                stage_status = stage_map.get(target_key, ticket.workflow_stage_status)

                if stage_status == StageStatus.AWAITING:
                    self.callbacks.complete_orchestration(
                        orch_run,
                        ticket,
                        status=OrchestrationRunStatus.SUCCEEDED,
                        message="Awaiting human approval",
                    )
                    self.session.refresh(orch_run)
                    return orch_run

                if is_agentless_stage(stage_def):
                    if stage_def.key == "done":
                        self.orch.finalize_workflow(ticket)
                        self.session.refresh(ticket)
                        stages_run += 1
                        continue
                    self.orch.enter_human_gate(ticket, stage_key=target_key)
                    self.session.refresh(ticket)
                    stages_run += 1
                    self.callbacks.complete_orchestration(
                        orch_run,
                        ticket,
                        status=OrchestrationRunStatus.SUCCEEDED,
                        message="Awaiting human approval",
                    )
                    self.session.refresh(orch_run)
                    return orch_run

                if stage_def.stage_type == "parallel":
                    ok, message = self._execute_parallel_stage(
                        ticket,
                        orch_run,
                        stage_def,
                        target_key,
                    )
                    stages_run += 1
                    if not ok:
                        self.callbacks.block_ticket(
                            orch_run,
                            ticket,
                            stage_key=target_key,
                            message=message or "Parallel stage failed",
                        )
                        self.session.refresh(orch_run)
                        return orch_run
                else:
                    agent_run = self.orch.start_run(
                        ticket,
                        stage_key=target_key,
                        orchestration_run_id=orch_run.id,
                    )
                    completed = self.executor.execute(agent_run, ticket)
                    self.session.refresh(ticket)
                    stages_run += 1

                    if stop_at_stage_key and target_key == stop_at_stage_key:
                        self.callbacks.complete_orchestration(
                            orch_run,
                            ticket,
                            status=OrchestrationRunStatus.SUCCEEDED,
                            message=f"Paused at stage {target_key}",
                        )
                        self.session.refresh(orch_run)
                        return orch_run

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

                next_key = StateMachine.next_stage_key(stages, target_key)
                if next_key:
                    gate_block = self._run_stage_gates(
                        ticket,
                        profile,
                        stage_def,
                        from_stage=target_key,
                        to_stage=next_key,
                    )
                    if gate_block:
                        self.callbacks.block_ticket(
                            orch_run,
                            ticket,
                            stage_key=target_key,
                            message=gate_block,
                        )
                        self.session.refresh(orch_run)
                        return orch_run

                if not next_key:
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

    def _run_stage_gates(
        self,
        ticket: Ticket,
        profile: OrchestrationProfile,
        stage_def: WorkflowStageDef,
        *,
        from_stage: str,
        to_stage: str,
    ) -> str:
        if not profile.gates.enabled:
            return ""

        workspace = self.session.get(Workspace, ticket.workspace_id)
        if not workspace:
            return "Workspace not found for gate execution"

        result = run_transition_gates(
            profile,
            workspace,
            ticket,
            from_stage=from_stage,
            to_stage=to_stage,
            stage_def=stage_def,
        )
        if result.ok:
            return ""

        detail = result.message or result.stderr or "Transition gate failed"
        if result.command:
            detail = f"{detail} (command: {result.command})"
        self.orch.finalize_stage(
            ticket,
            from_stage,
            status=StageStatus.BLOCKED,
            blocking_message=detail,
        )
        self.session.refresh(ticket)
        return detail

    def _execute_parallel_stage(
        self,
        ticket: Ticket,
        orch_run: OrchestrationRun,
        stage_def: WorkflowStageDef,
        stage_key: str,
    ) -> tuple[bool, str]:
        specs = stage_def.parallel_agents
        if not specs:
            self.orch.finalize_stage(ticket, stage_key, status=StageStatus.DONE)
            self.session.refresh(ticket)
            return True, ""

        workspace = self.session.get(Workspace, ticket.workspace_id)
        if workspace:
            from loregarden.services.git_branch import ensure_ticket_branch
            from loregarden.services.workspace_paths import resolve_workspace_root

            repo_root = resolve_workspace_root(workspace)
            if repo_root.is_dir():
                try:
                    ensure_ticket_branch(repo_root, ticket)
                except (ValueError, subprocess.CalledProcessError) as exc:
                    message = f"Failed to checkout branch: {exc}"
                    self.orch.finalize_stage(
                        ticket,
                        stage_key,
                        status=StageStatus.BLOCKED,
                        blocking_message=message,
                    )
                    self.session.refresh(ticket)
                    return False, message

        runs: list[AgentRun] = []
        for spec in specs:
            run = self.orch.start_run(
                ticket,
                stage_key=stage_key,
                orchestration_run_id=orch_run.id,
                agent_id=spec.agent_id,
                skill_name=spec.skill_name or stage_def.skill_name,
            )
            runs.append(run)

        failures: list[str] = []

        def _run_agent(run_id: str) -> tuple[str, str, str]:
            with Session(engine) as session:
                worker = CliAgentExecutor(session)
                run = session.get(AgentRun, run_id)
                if not run:
                    raise ValueError(f"Agent run not found: {run_id}")
                worker_ticket = session.get(Ticket, run.ticket_id)
                if not worker_ticket:
                    raise ValueError(f"Ticket not found for run: {run_id}")
                completed = worker.execute(
                    run,
                    worker_ticket,
                    advance_workflow=False,
                    skip_git_branch=True,
                )
                return completed.agent_id, completed.status.value, completed.stderr or ""

        def _collect_result(agent_label: str, result: tuple[str, str, str]) -> None:
            agent_id, status_value, stderr = result
            if status_value != RunStatus.SUCCEEDED.value:
                detail = stderr or "agent run failed"
                failures.append(f"{agent_id}: {detail}")

        from sqlmodel.pool import StaticPool

        if isinstance(engine.pool, StaticPool):
            for run in runs:
                try:
                    _collect_result(run.agent_id, _run_agent(run.id))
                except Exception as exc:
                    failures.append(f"{run.agent_id}: {exc}")
        else:
            with ThreadPoolExecutor(max_workers=max(1, len(runs))) as pool:
                future_map = {pool.submit(_run_agent, run.id): run.agent_id for run in runs}
                for future in as_completed(future_map):
                    agent_label = future_map[future]
                    try:
                        _collect_result(agent_label, future.result())
                    except Exception as exc:
                        failures.append(f"{agent_label}: {exc}")

        if failures:
            message = "; ".join(failures)
            self.orch.finalize_stage(
                ticket,
                stage_key,
                status=StageStatus.BLOCKED,
                blocking_message=message[:2000],
            )
            self.session.refresh(ticket)
            return False, message

        self.orch.finalize_stage(ticket, stage_key, status=StageStatus.DONE)
        self.session.refresh(ticket)
        return True, ""

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

    def _child_sort_key(self, ticket: Ticket) -> tuple:
        type_order = {
            WorkItemType.MILESTONE: 0,
            WorkItemType.FEATURE: 1,
            WorkItemType.CAPABILITY: 2,
            WorkItemType.TASK: 3,
            WorkItemType.BUG: 4,
        }
        return (type_order.get(ticket.work_item_type, 9), ticket.priority, ticket.external_id)

    def _ticket_workflow_complete(self, ticket: Ticket) -> bool:
        instance, stages = self.orch._resolve_stages(ticket)
        if not instance or not stages:
            return True
        stage_map = parse_stage_map(instance, stages)
        required = [s for s in stages if not s.optional]
        return all(
            stage_map.get(s.key, StageStatus.PENDING) in (StageStatus.DONE, StageStatus.WONT_DO)
            for s in required
        )

    def _orchestrate_incomplete_children(
        self,
        ticket: Ticket,
        profile: OrchestrationProfile,
    ) -> str | None:
        """Run direct child workflows sequentially before advancing the parent."""
        children = list(
            self.session.exec(select(Ticket).where(Ticket.parent_ticket_id == ticket.id)).all()
        )
        children.sort(key=self._child_sort_key)
        for child in children:
            if child.work_item_type not in WORKFLOW_WORK_ITEM_TYPES:
                continue
            self.orch.ensure_workflow_instance(child, commit=True)
            if self._ticket_workflow_complete(child):
                continue
            child_run = BuiltinOrchestrator(self.session).execute(
                child,
                profile,
                max_stages=None,
            )
            self.session.refresh(ticket)
            self.session.refresh(child)
            if child.state == TicketState.BLOCKED:
                return f"Child ticket blocked: {child.title}"
            if child_run.status == OrchestrationRunStatus.BLOCKED:
                return f"Child workflow blocked: {child.title}"
            if not self._ticket_workflow_complete(child):
                return f"Child workflow paused: {child.title}"
        return None
