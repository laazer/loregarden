"""Builtin orchestrator driver — top-level run invoking stage sub-agents via CLI."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.registry import DEBUGGER_AGENT_ID
from loregarden.core.state_machine import StateMachine
from loregarden.db.session import engine
from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    AgentRun,
    Artifact,
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
from loregarden.services.artifact_service import looks_like_test_output
from loregarden.services.evidence import has_evidence, resolve_head_sha
from loregarden.services.gate_runner import run_gate_autofix, run_transition_gates, strip_ansi
from loregarden.services.git_commit_push_service import commit_paths
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.stage_report import (
    StageReport,
    parse_stage_report,
    stage_report_artifact_content,
)
from loregarden.services.studio_routing import (
    is_agentless_stage,
    is_terminal_stage,
    took_light_route,
)
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlmodel import Session, select


class _GateDecision(Enum):
    """Outcome of the transition-gate check for a completed stage."""

    PASS = "pass"  # gate is clean (or was auto-fixed clean) — advance normally
    REROUTED = "rerouted"  # rerouted back to the stage for an inline auto-fix retry
    BLOCKED = "blocked"  # automatic fixes exhausted — rerouted and paused for a human


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
                    return self._pause_orchestration(orch_run, ticket, message=child_pause)

                instance, stages, recovered_stage_key = self._resolve_stages_with_recovery(ticket)
                if not instance or not stages:
                    break

                stage_map = parse_stage_map(instance, stages)
                target_key = self._next_executable_stage(ticket, stages, stage_map)
                if not target_key:
                    return self._pause_orchestration(orch_run, ticket)

                if limit > 0 and stages_run >= limit:
                    return self._pause_orchestration(
                        orch_run, ticket, message=f"Paused after {stages_run} stage(s)"
                    )

                stage_def = next(s for s in stages if s.key == target_key)
                stage_status = stage_map.get(target_key, ticket.workflow_stage_status)

                if stage_status == StageStatus.AWAITING:
                    return self._pause_orchestration(
                        orch_run, ticket, message="Awaiting human approval"
                    )

                if is_agentless_stage(stage_def):
                    handled = self._handle_agentless_stage(ticket, orch_run, stage_def, target_key)
                    stages_run += 1
                    if handled is None:
                        continue
                    return handled

                if stage_def.stage_type == "parallel":
                    stopped = self._run_parallel_stage_or_stop(
                        ticket,
                        orch_run,
                        stage_def,
                        target_key,
                        auto_approve=auto_approve,
                        resuming=(target_key == recovered_stage_key),
                    )
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
                    return self._pause_orchestration(
                        orch_run, ticket, message="Awaiting human approval"
                    )

                next_route = StateMachine.resolve_next_stage_key(
                    stages,
                    self.orch._resolve_transitions(ticket),
                    target_key,
                    outcome="pass",
                )
                next_key = next_route.to_key if next_route else None
                if next_key:
                    decision = self._run_gates_with_autofix(
                        ticket,
                        profile,
                        stage_def,
                        instance,
                        stages,
                        orch_run,
                        from_stage=target_key,
                        to_stage=next_key,
                    )
                    if decision is _GateDecision.BLOCKED:
                        self.session.refresh(orch_run)
                        return orch_run
                    if decision is _GateDecision.REROUTED:
                        # Stage was routed back to itself for an inline retry;
                        # let the loop re-run it this same pass.
                        continue
                    # _GateDecision.PASS falls through to advance normally.

                if not next_key:
                    return self._pause_orchestration(orch_run, ticket)

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

    def _pause_orchestration(
        self, orch_run: OrchestrationRun, ticket: Ticket, *, message: str = ""
    ) -> OrchestrationRun:
        """Mark this orchestration run SUCCEEDED (the run itself didn't fail —
        it's just pausing here: awaiting approval, hit its stage limit, or has
        nothing left to do this pass) and return it for the caller to return."""
        self.callbacks.complete_orchestration(
            orch_run,
            ticket,
            status=OrchestrationRunStatus.SUCCEEDED,
            message=message,
        )
        self.session.refresh(orch_run)
        return orch_run

    def _resolve_stages_with_recovery(
        self, ticket: Ticket
    ) -> tuple[WorkflowInstance | None, list[WorkflowStageDef], str | None]:
        """Resolve the ticket's workflow stages, recovering a stage BLOCKED only
        by a server restart (not a genuine failure) before the caller picks the
        next stage to run. Returns the recovered stage key alongside the
        (possibly re-resolved) instance/stages, so the caller can tell a
        parallel stage it's being resumed rather than started fresh.
        """
        instance, stages = self.orch._resolve_stages(ticket)
        if not instance or not stages:
            return instance, stages, None
        recovered_stage_key = self._recover_interrupted_stage(ticket, instance, stages)
        if recovered_stage_key:
            self.session.refresh(ticket)
            instance, stages = self.orch._resolve_stages(ticket)
        return instance, stages, recovered_stage_key

    def _handle_agentless_stage(
        self,
        ticket: Ticket,
        orch_run: OrchestrationRun,
        stage_def: WorkflowStageDef,
        target_key: str,
    ) -> OrchestrationRun | None:
        """Handle a stage with no agent to run (the final `done` stage, or a
        human-approval gate). Returns None if the caller should `continue` the
        loop (workflow just finished), else the `orch_run` to return now.
        """
        if is_terminal_stage(stage_def):
            self.orch.finalize_workflow(ticket)
            self.session.refresh(ticket)
            return None
        self.orch.enter_human_gate(ticket, stage_key=target_key)
        self.session.refresh(ticket)
        return self._pause_orchestration(orch_run, ticket, message="Awaiting human approval")

    def _run_parallel_stage_or_stop(
        self,
        ticket: Ticket,
        orch_run: OrchestrationRun,
        stage_def: WorkflowStageDef,
        target_key: str,
        *,
        auto_approve: bool,
        resuming: bool,
    ) -> bool:
        """Run a parallel stage. Returns True if the caller should stop and
        return `orch_run` now (the stage failed), False to keep going.
        """
        ok, message = self._execute_parallel_stage(
            ticket,
            orch_run,
            stage_def,
            target_key,
            auto_approve=auto_approve,
            resuming=resuming,
        )
        if ok:
            return False
        self.callbacks.block_ticket(
            orch_run,
            ticket,
            stage_key=target_key,
            message=message or "Parallel stage failed",
        )
        return True

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

    def _run_gates(
        self,
        ticket: Ticket,
        profile: OrchestrationProfile,
        workspace: Workspace,
        stage_def: WorkflowStageDef,
        from_stage: str,
        to_stage: str,
    ) -> str:
        """Run the transition gates once. Returns "" if they pass, else a cleaned,
        human-readable failure detail. Pure — no ticket/stage mutation, so it can
        be re-run after an auto-fix pass to check whether the fix cleared it."""
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
        return strip_ansi(detail)

    def _gate_failure_artifact_title(self, stage_key: str) -> str:
        return f"Transition gate failed — {stage_key}"

    def _persisted_gate_fix_attempts(self, ticket: Ticket, stage_key: str) -> int:
        """Count prior automatic gate-fix retries for this stage, persisted via
        the error artifacts _reroute_for_agent_fix/_block_after_gate_failure
        attach — so the retry budget holds across separate orchestration runs,
        not just within a single `execute()` call. A function-local counter
        resets every time a new run starts (e.g. an operator or auto-resume
        re-triggers orchestration after a pause), letting a stage that can
        never pass its gate (a persistent environment issue, not something an
        agent can fix by editing code) cycle indefinitely instead of ever
        durably giving up.
        """
        return len(
            self.session.exec(
                select(Artifact)
                .where(Artifact.ticket_id == ticket.id)
                .where(Artifact.kind == "error")
                .where(Artifact.title == self._gate_failure_artifact_title(stage_key))
            ).all()
        )

    def _missing_evidence_detail(
        self,
        ticket: Ticket,
        stage_def: WorkflowStageDef,
        stages: list[WorkflowStageDef] | None = None,
    ) -> str:
        """Why this stage cannot pass yet for want of proof, or "" when satisfied.

        Scoped to the current HEAD: evidence carried over from an earlier commit
        proves nothing about the code being gated. Note the agent's own work is
        still uncommitted at gate time, so this catches proof left over from a
        previous stage or commit rather than proof captured a few edits ago.
        """
        required = [kind for kind in (stage_def.required_evidence or []) if kind]
        if not required:
            return ""

        # Light work is exempt, on the same reasoning that exempts it from
        # verification: triage already judged the ticket trivial enough to branch
        # past planning, and demanding a captured real-surface run for a typo
        # costs more than the proof is worth. Heavy work still has to show it.
        if stages and took_light_route(ticket, stages):
            return ""

        commit_sha = resolve_head_sha(self.session, ticket)
        missing = [
            kind
            for kind in required
            if not has_evidence(self.session, ticket, commit_sha=commit_sha, evidence_kind=kind)
        ]
        if not missing:
            return ""
        return (
            f"Stage '{stage_def.key}' requires evidence for the current commit that is "
            f"missing: {', '.join(missing)}. Attach it with loregarden_attach_evidence "
            "— green tests alone do not show the change works."
        )

    def _run_gates_with_autofix(
        self,
        ticket: Ticket,
        profile: OrchestrationProfile,
        stage_def: WorkflowStageDef,
        instance: WorkflowInstance,
        stages: list[WorkflowStageDef],
        orch_run: OrchestrationRun,
        *,
        from_stage: str,
        to_stage: str,
    ) -> _GateDecision:
        """Run the transition gate for a just-completed stage and, if it fails,
        try to fix it automatically before pulling in a human.

        Order: mechanical fixers (ruff --fix, formatters, ...) → re-run gate; if
        clean, commit the fix and advance. Otherwise hand the residual failure
        back to the stage's own agent for a bounded number of inline retries.
        Only once those are exhausted do we fall back to today's behaviour —
        reroute for rework and pause for a human.
        """
        workspace = self.session.get(Workspace, ticket.workspace_id)
        if not workspace:
            # Can't run gates without a workspace; don't wedge the pipeline over it.
            return _GateDecision.PASS

        # Missing proof is reported as a gate failure so it inherits the whole
        # recovery path: the stage's own agent is handed the reason and gets a
        # bounded number of tries to attach it before a human is pulled in.
        # Checked independently of profile.gates.enabled — a stage only opts in by
        # declaring required_evidence, so nothing that has not asked is affected.
        detail = self._missing_evidence_detail(ticket, stage_def, stages)
        if not detail and profile.gates.enabled:
            detail = self._run_gates(ticket, profile, workspace, stage_def, from_stage, to_stage)
        if not detail:
            return _GateDecision.PASS

        # Gate failed. First, let mechanical fixers have a go — these clear the
        # "basic problems" (imports, formatting, trivial lint) with no agent run.
        if profile.gates.autofix_commands:
            autofix = run_gate_autofix(
                profile,
                workspace,
                ticket,
                from_stage=from_stage,
                to_stage=to_stage,
                stage_def=stage_def,
            )
            if autofix.ran:
                residual = self._run_gates(
                    ticket, profile, workspace, stage_def, from_stage, to_stage
                )
                if not residual:
                    self._commit_autofix(ticket, from_stage, autofix.output)
                    return _GateDecision.PASS
                detail = residual

        # Fixers didn't (or couldn't) clear it. Route back to the stage's own
        # agent with the gate errors in context, up to a bounded number of
        # tries — counted durably (see _persisted_gate_fix_attempts) so the
        # budget can't be refreshed just by starting a new orchestration run.
        attempts = self._persisted_gate_fix_attempts(ticket, from_stage)
        if (
            profile.gates.autofix_agent_fallback
            and attempts < profile.gates.autofix_max_agent_attempts
        ):
            self._reroute_for_agent_fix(ticket, instance, stages, orch_run, from_stage, detail)
            return _GateDecision.REROUTED

        # Out of automatic options — reroute for rework and pause for a human.
        self._block_after_gate_failure(ticket, instance, stages, orch_run, from_stage, detail)
        return _GateDecision.BLOCKED

    def _ticket_changed_paths(self, ticket: Ticket) -> list[str]:
        """Every path this ticket's runs have touched.

        Union across runs because a gate fires after several stages have each
        left work in the tree, and the fix belongs with the work that provoked
        it. Paths no run recorded are someone else's and stay uncommitted.
        """
        rows = self.session.exec(
            select(AgentRun.changed_paths_json).where(AgentRun.ticket_id == ticket.id)
        ).all()
        paths: set[str] = set()
        for raw in rows:
            paths.update(json.loads(raw or "[]"))
        return sorted(paths)

    def _commit_autofix(self, ticket: Ticket, from_stage: str, output: str) -> None:
        """Commit the mechanical fixer diff onto the ticket branch and note it as
        a context artifact, so the invisible fix is a first-class commit rather
        than an uncommitted working-tree change."""
        try:
            committed = commit_paths(
                self.session,
                ticket,
                message=(
                    f"chore({from_stage}): auto-fix static-analysis gate [{ticket.external_id}]"
                ),
                paths=self._ticket_changed_paths(ticket),
            )
        except ValueError:
            committed = False
        if committed:
            self.callbacks.attach_artifact(
                ticket,
                kind="context",
                title=f"Auto-fixed static-analysis gate — {from_stage}",
                content={
                    "title": f"Auto-fixed static-analysis gate — {from_stage}",
                    "rows": [
                        {"k": "Stage", "v": from_stage},
                        {
                            "k": "Message",
                            "v": output or "Mechanical fixers cleared the transition gate.",
                        },
                    ],
                },
            )

    def _gate_failure_agent(self, detail: str) -> str:
        """Who should take a failing gate: "" for the stage's own agent.

        A lint or format failure is the stage's own mess and it can clear it. A
        failing test is a different job — the agent that just declared success is
        the one whose model of the code is wrong, and asking it again tends to
        produce the nearest change that makes the red go away.
        """
        return DEBUGGER_AGENT_ID if looks_like_test_output(detail) else ""

    def _reroute_for_agent_fix(
        self,
        ticket: Ticket,
        instance: WorkflowInstance,
        stages: list[WorkflowStageDef],
        orch_run: OrchestrationRun,
        from_stage: str,
        detail: str,
    ) -> None:
        """Mechanical fixers couldn't clear the gate. Route back to this stage so
        its agent gets another pass — this time with the gate failure in its
        context — and let the run loop re-run it inline instead of stalling for a
        human. The full gate output still goes to the Errors tab; blocking_issues
        carries a trimmed, fix-directed copy (capped by apply_stage_route) so the
        re-run agent can actually act on it.
        """
        self.callbacks.attach_artifact(
            ticket,
            kind="error",
            title=self._gate_failure_artifact_title(from_stage),
            content={
                "message": detail,
                "run_code": "",
                "agent_id": "",
                "stage_key": from_stage,
                "command": "",
            },
        )
        handoff_agent = self._gate_failure_agent(detail)
        if handoff_agent:
            blocking = (
                f"The '{from_stage}' stage reported success, then its tests failed. Find the "
                f"root cause from observed runtime state and fix that — do not delete, skip, "
                f"or loosen a test to get a pass. Report `pass` once it is green:\n\n{detail}"
            )
        else:
            blocking = (
                f"The '{from_stage}' stage passed its agent but failed the static-analysis gate "
                f"on the way to the next stage, and automatic fixers couldn't resolve it. "
                f"Fix these issues and report `pass`:\n\n{detail}"
            )
        apply_stage_route(
            ticket,
            instance,
            stages,
            self.orch._resolve_transitions(ticket),
            from_key=from_stage,
            outcome="reject",
            next_stage_key=from_stage,
            next_agent=handoff_agent,
            blocking_issues=blocking,
            orch_run=orch_run,
        )
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()

    def _block_after_gate_failure(
        self,
        ticket: Ticket,
        instance: WorkflowInstance,
        stages: list[WorkflowStageDef],
        orch_run: OrchestrationRun,
        from_stage: str,
        detail: str,
    ) -> None:
        """Automatic fixes are exhausted. Reroute back to the stage (self-redo)
        and pause for a human — the pre-existing gate-failure behaviour. The raw
        gate output goes to the Errors tab; blocking_issues, rendered directly in
        the workflow pane, stays a short pointer rather than a wall of text.
        """
        self.callbacks.attach_artifact(
            ticket,
            kind="error",
            title=self._gate_failure_artifact_title(from_stage),
            content={
                "message": detail,
                "run_code": "",
                "agent_id": "",
                "stage_key": from_stage,
                "command": "",
            },
        )
        apply_stage_route(
            ticket,
            instance,
            stages,
            self.orch._resolve_transitions(ticket),
            from_key=from_stage,
            outcome="reject",
            next_stage_key=from_stage,
            blocking_issues=(
                f"Transition gate failed at '{from_stage}' — see the Errors tab for details."
            ),
            orch_run=orch_run,
        )
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        self.callbacks.complete_orchestration(
            orch_run,
            ticket,
            status=OrchestrationRunStatus.SUCCEEDED,
            message=f"Transition gate failed at '{from_stage}'; rerouted for rework",
        )

    def _execute_parallel_stage(
        self,
        ticket: Ticket,
        orch_run: OrchestrationRun,
        stage_def: WorkflowStageDef,
        stage_key: str,
        *,
        auto_approve: bool = False,
        resuming: bool = False,
    ) -> tuple[bool, str]:
        specs = stage_def.parallel_agents
        if not specs:
            self.orch.finalize_stage(ticket, stage_key, status=StageStatus.DONE)
            self.session.refresh(ticket)
            return True, ""

        pending_specs = (
            self._incomplete_parallel_specs(ticket, stage_def, stage_key, specs)
            if resuming
            else specs
        )
        if not pending_specs:
            # Resuming after an interruption, but every member had already
            # succeeded before the crash — nothing left to redo.
            self.orch.finalize_stage(ticket, stage_key, status=StageStatus.DONE)
            self.session.refresh(ticket)
            return True, ""

        branch_error = self._checkout_branch_for_parallel_stage(ticket, stage_key)
        if branch_error:
            return False, branch_error

        runs = self._start_parallel_stage_runs(
            ticket, orch_run, stage_def, stage_key, pending_specs, auto_approve=auto_approve
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

    def _incomplete_parallel_specs(
        self, ticket: Ticket, stage_def: WorkflowStageDef, stage_key: str, specs
    ):
        """Filter a parallel stage's members down to those not already done.

        Only meaningful when resuming a stage interrupted mid-run (e.g. a server
        restart) — a member's most recent run for this exact ticket+stage already
        reflects the current attempt, since a genuine reject/rework reroute always
        starts a fresh attempt for every member instead of reusing this path. Reusing
        an already-succeeded member here avoids redoing work a crash didn't touch,
        while whatever remains still runs concurrently via the normal parallel path.
        """
        incomplete = []
        for spec in specs:
            latest = self.session.exec(
                select(AgentRun)
                .where(
                    AgentRun.ticket_id == ticket.id,
                    AgentRun.stage_key == stage_key,
                    AgentRun.agent_id == spec.agent_id,
                    # Lanes may share an agent and differ only by skill — three
                    # planners under different lenses, say. Matching on agent
                    # alone would let one finished lane mark its siblings done.
                    AgentRun.skill_name == (spec.skill_name or stage_def.skill_name),
                )
                .order_by(AgentRun.created_at.desc())
            ).first()
            if latest is None or latest.status != RunStatus.SUCCEEDED:
                incomplete.append(spec)
                continue
            report = parse_stage_report(latest.stdout)
            if report and report.status != "pass":
                incomplete.append(spec)
        return incomplete

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
            elif report and report.status in ("fail", "needs_rework", "blocked"):
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

    def _recover_interrupted_stage(self, ticket: Ticket, instance, stages) -> str | None:
        """Clear a stage blocked only by a server restart, not a genuine failure.

        fail_interrupted_runs() marks an orphaned AgentRun FAILED and its stage
        BLOCKED with INTERRUPTED_RUN_MESSAGE, without touching ticket.state — unlike
        block_ticket(), which real agent/test failures go through and which does set
        ticket.state to BLOCKED (and is excluded by the loop's state check above this
        method's call site). _next_executable_stage() otherwise refuses to touch any
        BLOCKED stage, so without this, Continue Run would silently no-op forever on
        a stage that only needs a retry, per that message's own guidance.

        Returns the recovered stage key (so callers can tell _execute_parallel_stage
        this is a resume, not a fresh attempt) or None if nothing needed recovering.
        """
        from loregarden.services.run_service import INTERRUPTED_RUN_MESSAGE

        cursor_key = ticket.workflow_stage_key
        if not cursor_key:
            return None
        stage_map = parse_stage_map(instance, stages)
        if stage_map.get(cursor_key) != StageStatus.BLOCKED:
            return None
        if ticket.blocking_issues != INTERRUPTED_RUN_MESSAGE:
            return None

        set_stage_status(ticket, instance, stages, cursor_key, StageStatus.PENDING)
        ticket.blocking_issues = ""
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        return cursor_key

    def _next_executable_stage(self, ticket: Ticket, stages, stage_map) -> str | None:
        """Pick the next stage to run: earliest-in-template-order wins.

        Deliberately ignores ticket.workflow_stage_key as a shortcut here — a
        stage can be manually re-run independently of the ticket's cursor (the
        UI exposes a Run/Re-Run button per stage), which can leave an earlier
        stage PENDING while the cursor already points at a later one. Trusting
        the cursor in that state would silently skip the earlier, still-
        unresolved stage. Always scanning in order means the cursor being
        "ahead" of an unresolved stage self-heals on the next orchestration
        pass instead of compounding.
        """
        ordered = sorted(stages, key=lambda s: s.order)
        keys = [s.key for s in ordered]

        for status in (StageStatus.RUNNING, StageStatus.AWAITING, StageStatus.BLOCKED):
            for key in keys:
                if stage_map.get(key) == status:
                    if status == StageStatus.BLOCKED:
                        return None
                    return key

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
