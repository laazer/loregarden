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
    WorkflowInstance,
    WorkflowStageDef,
    WorkItemType,
    Workspace,
)
from loregarden.services.gate_runner import run_transition_gates
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.stage_report import (
    StageReport,
    parse_stage_report,
    stage_report_artifact_content,
)
from loregarden.services.studio_service import is_agentless_stage
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
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

                if self._recover_interrupted_stage(ticket, instance, stages):
                    self.session.refresh(ticket)
                    instance, stages = self.orch._resolve_stages(ticket)

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
                        auto_approve=auto_approve,
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
                    stopped = self._run_sequential_stage(
                        ticket,
                        orch_run,
                        target_key,
                        auto_approve=auto_approve,
                        stop_at_stage_key=stop_at_stage_key,
                    )
                    stages_run += 1
                    if stopped:
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

                next_route = StateMachine.resolve_next_stage_key(
                    stages,
                    self.orch._resolve_transitions(ticket),
                    target_key,
                    outcome="pass",
                )
                next_key = next_route.to_key if next_route else None
                if next_key:
                    gate_block = self._run_stage_gates(
                        ticket,
                        profile,
                        stage_def,
                        from_stage=target_key,
                        to_stage=next_key,
                    )
                    if gate_block:
                        self._reroute_after_gate_failure(
                            ticket, instance, stages, orch_run, target_key, gate_block
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

    def _run_sequential_stage(
        self,
        ticket: Ticket,
        orch_run: OrchestrationRun,
        target_key: str,
        *,
        auto_approve: bool,
        stop_at_stage_key: str | None,
    ) -> bool:
        """Run a single-agent stage. Returns True if the caller should stop
        and return `orch_run` now (paused at `stop_at_stage_key`, or the
        sub-agent failed), False to keep processing this pass normally.
        """
        agent_run = self.orch.start_run(
            ticket,
            stage_key=target_key,
            orchestration_run_id=orch_run.id,
            auto_approve=auto_approve,
        )
        completed = self.executor.execute(agent_run, ticket)
        self.session.refresh(ticket)

        if stop_at_stage_key and target_key == stop_at_stage_key:
            self.callbacks.complete_orchestration(
                orch_run,
                ticket,
                status=OrchestrationRunStatus.SUCCEEDED,
                message=f"Paused at stage {target_key}",
            )
            return True

        if completed.status != RunStatus.SUCCEEDED:
            self.callbacks.block_ticket(
                orch_run,
                ticket,
                stage_key=target_key,
                message=completed.stderr or "Stage sub-agent failed",
            )
            return True

        return False

    def _reroute_after_gate_failure(
        self,
        ticket: Ticket,
        instance: WorkflowInstance,
        stages: list[WorkflowStageDef],
        orch_run: OrchestrationRun,
        target_key: str,
        gate_block: str,
    ) -> None:
        """A transition gate (e.g. lint/static-analysis) failing isn't the agent
        reporting bad work upstream — it's this stage's own output failing an
        objective check. Reroute back to the same stage (self-redo) rather than
        hard-blocking, so the ticket gets another automatic pass instead of
        stalling for a human.
        """
        apply_stage_route(
            ticket,
            instance,
            stages,
            self.orch._resolve_transitions(ticket),
            from_key=target_key,
            outcome="reject",
            next_stage_key=target_key,
            blocking_issues=gate_block,
            orch_run=orch_run,
        )
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        self.callbacks.complete_orchestration(
            orch_run,
            ticket,
            status=OrchestrationRunStatus.SUCCEEDED,
            message=f"Transition gate failed at '{target_key}'; rerouted for rework",
        )

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
        *,
        auto_approve: bool = False,
    ) -> tuple[bool, str]:
        specs = stage_def.parallel_agents
        if not specs:
            self.orch.finalize_stage(ticket, stage_key, status=StageStatus.DONE)
            self.session.refresh(ticket)
            return True, ""

        branch_error = self._checkout_branch_for_parallel_stage(ticket, stage_key)
        if branch_error:
            return False, branch_error

        runs = self._start_parallel_stage_runs(
            ticket, orch_run, stage_def, stage_key, specs, auto_approve=auto_approve
        )
        failures, reports = self._run_and_collect_parallel_results(runs)

        for agent_id, report in reports:
            self.callbacks.attach_artifact(
                ticket,
                kind="context",
                title=f"Stage report — {stage_key} ({agent_id})",
                content=stage_report_artifact_content(stage_key, report),
            )

        if failures:
            return self._handle_parallel_stage_failures(ticket, stage_key, failures, reports)

        self.orch.finalize_stage(ticket, stage_key, status=StageStatus.DONE)
        self.session.refresh(ticket)
        return True, ""

    def _checkout_branch_for_parallel_stage(self, ticket: Ticket, stage_key: str) -> str:
        """Ensure the ticket's branch is checked out before spawning parallel agents.

        Returns an error message (and finalizes the stage as BLOCKED) on failure,
        else an empty string.
        """
        workspace = self.session.get(Workspace, ticket.workspace_id)
        if not workspace:
            return ""

        from loregarden.services.git_branch import ensure_ticket_branch
        from loregarden.services.workspace_paths import resolve_workspace_root

        repo_root = resolve_workspace_root(workspace)
        if not repo_root.is_dir():
            return ""

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
            return message
        return ""

    def _start_parallel_stage_runs(
        self,
        ticket: Ticket,
        orch_run: OrchestrationRun,
        stage_def: WorkflowStageDef,
        stage_key: str,
        specs,
        *,
        auto_approve: bool,
    ) -> list[AgentRun]:
        runs: list[AgentRun] = []
        for spec in specs:
            run = self.orch.start_run(
                ticket,
                stage_key=stage_key,
                orchestration_run_id=orch_run.id,
                agent_id=spec.agent_id,
                skill_name=spec.skill_name or stage_def.skill_name,
                auto_approve=auto_approve,
            )
            runs.append(run)
        return runs

    def _run_and_collect_parallel_results(
        self, runs: list[AgentRun]
    ) -> tuple[list[str], list[tuple[str, StageReport]]]:
        failures: list[str] = []
        reports: list[tuple[str, StageReport]] = []

        def _run_agent(run_id: str) -> tuple[str, str, str, str]:
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
                return (
                    completed.agent_id,
                    completed.status.value,
                    completed.stderr or "",
                    completed.stdout or "",
                )

        def _collect_result(agent_label: str, result: tuple[str, str, str, str]) -> None:
            agent_id, status_value, stderr, stdout = result
            report = parse_stage_report(stdout)
            if report:
                reports.append((agent_id, report))
            if status_value != RunStatus.SUCCEEDED.value:
                failures.append(f"{agent_id}: {stderr or 'agent run failed'}")
            elif report and report.status in ("fail", "needs_rework"):
                failures.append(f"{agent_id}: {report.reroute_context or 'agent reported failure'}")

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

        return failures, reports

    def _handle_parallel_stage_failures(
        self,
        ticket: Ticket,
        stage_key: str,
        failures: list[str],
        reports: list[tuple[str, StageReport]],
    ) -> tuple[bool, str]:
        message = "; ".join(failures)
        transitions = self.orch._resolve_transitions(ticket)

        # Prefer an agent-specified reroute target (highest-confidence
        # among reject/needs_rework reports) over the template's `reject`
        # transition — apply_stage_route falls back to the template route,
        # then to the immediately preceding stage, when this is empty.
        rejecting = [
            (agent_id, r)
            for agent_id, r in reports
            if r.status in ("fail", "needs_rework") and r.reroute_to_stage
        ]
        rejecting.sort(key=lambda pair: pair[1].confidence, reverse=True)
        agent_to_key = rejecting[0][1].reroute_to_stage if rejecting else ""
        agent_context = rejecting[0][1].reroute_context if rejecting else ""

        template_route = StateMachine.resolve_transition_target(transitions, stage_key, "reject")
        to_key = agent_to_key or (template_route[0] if template_route else "")
        transition_agent = template_route[1] if template_route else ""

        instance, stages = self.orch._resolve_stages(ticket)
        if instance and stages:
            try:
                apply_stage_route(
                    ticket,
                    instance,
                    stages,
                    transitions,
                    from_key=stage_key,
                    outcome="reject",
                    next_stage_key=to_key,
                    next_agent=transition_agent or ticket.next_agent,
                    blocking_issues=(agent_context or message)[:2000],
                )
                self.session.add(ticket)
                self.session.add(instance)
                self.session.commit()
                self.session.refresh(ticket)
                return True, message
            except ValueError:
                pass  # first-in-order stage, nowhere to fall back to — BLOCKED below

        self.orch.finalize_stage(
            ticket,
            stage_key,
            status=StageStatus.BLOCKED,
            blocking_message=message[:2000],
        )
        self.session.refresh(ticket)
        return False, message

    def _recover_interrupted_stage(self, ticket: Ticket, instance, stages) -> bool:
        """Clear a stage blocked only by a server restart, not a genuine failure.

        fail_interrupted_runs() marks an orphaned AgentRun FAILED and its stage
        BLOCKED with INTERRUPTED_RUN_MESSAGE, without touching ticket.state — unlike
        block_ticket(), which real agent/test failures go through and which does set
        ticket.state to BLOCKED (and is excluded by the loop's state check above this
        method's call site). _next_executable_stage() otherwise refuses to touch any
        BLOCKED stage, so without this, Continue Run would silently no-op forever on
        a stage that only needs a retry, per that message's own guidance.
        """
        from loregarden.services.run_service import INTERRUPTED_RUN_MESSAGE

        cursor_key = ticket.workflow_stage_key
        if not cursor_key:
            return False
        stage_map = parse_stage_map(instance, stages)
        if stage_map.get(cursor_key) != StageStatus.BLOCKED:
            return False
        if ticket.blocking_issues != INTERRUPTED_RUN_MESSAGE:
            return False

        set_stage_status(ticket, instance, stages, cursor_key, StageStatus.PENDING)
        ticket.blocking_issues = ""
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        return True

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
