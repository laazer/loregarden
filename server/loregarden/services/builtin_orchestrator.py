"""Builtin orchestrator driver — top-level run invoking stage sub-agents via CLI."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.registry import DEBUGGER_AGENT_ID
from loregarden.core.state_machine import StateMachine
from loregarden.db.session import engine
from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    AgentRun,
    GateFaultAttribution,
    GateOutcome,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.artifact_service import looks_like_test_output, record_blocking_issue
from loregarden.services.evidence import has_evidence, resolve_head_sha
from loregarden.services.gate_attribution import attribute_gate_failure
from loregarden.services.gate_observability import (
    clean_gate_detail,
    record_gate_evaluation,
    run_gates_detail,
)
from loregarden.services.gate_runner import run_gate_autofix, run_transition_gates
from loregarden.services.git_commit_push_service import commit_paths
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.parallel_stage import (
    ParallelMemberResult,
    latest_member_run,
    member_passed,
    member_result_from_run,
    prepare_tree_for_parallel_stage,
    reconcile_parallel_stage,
)
from loregarden.services.run_cancellation import orchestration_cancel_requested
from loregarden.services.run_interruption import blocked_by_interruption, interrupted_stage_key
from loregarden.services.run_lease import lease_renewal
from loregarden.services.stage_retry_budget import (
    count_gate_fix_attempts,
    enforce_stage_retry_budget,
    gate_failure_artifact_title,
)
from loregarden.services.studio_routing import (
    is_agentless_stage,
    is_terminal_stage,
    took_light_route,
)
from loregarden.services.subtree_auto_run import (
    SubtreeBudget,
    auto_resolve_awaiting_gate,
    finalize_aggregator_ticket,
    order_children_for_subtree,
    ticket_workflow_complete,
)
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import (
    next_executable_stage,
    parse_stage_map,
    set_stage_status,
)
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
        timeout_seconds: int | None = None,
        _subtree_budget: SubtreeBudget | None = None,
    ) -> OrchestrationRun:
        limit = max_stages if max_stages is not None else profile.max_stages_per_run
        # Only the outermost call in a subtree creates the budget; every
        # nested execute() this call makes (via _orchestrate_incomplete_children)
        # receives and shares this same instance, so the cap holds across the
        # whole tree instead of resetting per ticket.
        budget = SubtreeBudget.for_root(_subtree_budget, profile)
        orch_run = self.callbacks.start_orchestration_run(
            ticket,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            profile_slug=profile.slug,
            auto_approve=auto_approve,
            stop_at_stage_key=stop_at_stage_key or "",
            timeout_override_seconds=timeout_seconds,
        )
        self.session.refresh(ticket)
        # Prefer the claim's stored timeout when this execute was dispatched from
        # a lane that already answered the dialog.
        agent_timeout = (
            orch_run.timeout_override_seconds
            if orch_run.timeout_override_seconds is not None
            else timeout_seconds
        )

        stages_run = 0
        try:
            while True:
                if self._should_stop_orchestration(ticket, orch_run):
                    break

                child_pause = _orchestrate_incomplete_children(
                    self,
                    ticket,
                    profile,
                    auto_approve=auto_approve,
                    timeout_seconds=agent_timeout,
                    subtree_budget=budget,
                )
                if child_pause:
                    return self._pause_orchestration(orch_run, ticket, message=child_pause)

                # A ticket with child tickets is a pure aggregator: its children
                # carry the work and it never runs its own workflow stages. Running
                # them would create an unused ticket branch (ensure_ticket_branch
                # fires per stage agent) and sweep a parent commit onto whatever
                # branch is checked out — the last child's — and they cannot even
                # decompose the parent, since orchestrated agents are denied
                # loregarden_create_ticket (decomposition happens in Ticket Studio,
                # before orchestration). Reaching here means every child is complete
                # (the pause above returns otherwise), so finalize the parent and stop.
                if self._has_child_tickets(ticket):
                    return self._finalize_aggregator_parent(orch_run, ticket)

                instance, stages, recovered_stage_key = self._resolve_stages_with_recovery(ticket)
                if not instance or not stages:
                    break

                stage_map = parse_stage_map(instance, stages)
                target_key = next_executable_stage(stages, stage_map)
                if not target_key:
                    return self._pause_orchestration(orch_run, ticket)

                if limit > 0 and stages_run >= limit:
                    return self._pause_orchestration(
                        orch_run, ticket, message=f"Paused after {stages_run} stage(s)"
                    )

                stage_def = next(s for s in stages if s.key == target_key)
                target_is_terminal = is_terminal_stage(stage_def)
                bound_pause = budget.pause_message(terminal=target_is_terminal)
                if bound_pause:
                    return self._pause_orchestration(orch_run, ticket, message=bound_pause)

                budget_block = enforce_stage_retry_budget(
                    self.session,
                    self.callbacks,
                    orch_run,
                    ticket,
                    target_key,
                    profile.retry_budget,
                )
                if budget_block is not None:
                    return budget_block

                stage_status = stage_map.get(target_key, ticket.workflow_stage_status)

                if stage_status == StageStatus.AWAITING:
                    if auto_approve and auto_resolve_awaiting_gate(
                        self.session, ticket, orch_run, target_key
                    ):
                        continue
                    return self._pause_orchestration(
                        orch_run, ticket, message="Awaiting human approval"
                    )

                if is_agentless_stage(stage_def):
                    handled = self._handle_agentless_stage(
                        ticket, orch_run, stage_def, target_key, auto_approve=auto_approve
                    )
                    stages_run += 1
                    budget.consume(terminal=target_is_terminal)
                    if handled is None:
                        continue
                    return handled

                stopped = self._dispatch_agent_stage(
                    ticket,
                    orch_run,
                    stage_def,
                    target_key,
                    auto_approve=auto_approve,
                    timeout_seconds=agent_timeout,
                    stop_at_stage_key=stop_at_stage_key,
                    resuming=(target_key == recovered_stage_key),
                )
                stages_run += 1
                budget.consume(terminal=target_is_terminal)
                if stopped:
                    self.session.refresh(orch_run)
                    return orch_run

                # A scope-denial reroute re-armed this stage to PENDING for the
                # sibling implementer (permission_bridge._try_scope_reroute), and
                # _run_sequential_stage already refreshed the ticket. Re-dispatch it
                # rather than advancing past it as if it passed: running the exit
                # gate here would block on work the sibling hasn't done yet. (A
                # parallel stage never sets this pin, so its flow is untouched.)
                if ticket.scope_reroute_agent:
                    continue

                advanced = self._advance_after_stage(
                    ticket, profile, stage_def, orch_run, target_key
                )
                if advanced is not None:
                    return advanced

            self._complete_run(orch_run, ticket)
        except Exception as exc:
            self.callbacks.block_ticket(
                orch_run,
                ticket,
                message=str(exc),
            )
        self.session.refresh(orch_run)
        return orch_run

    def _complete_run(self, orch_run: OrchestrationRun, ticket: Ticket) -> OrchestrationRun:
        """Close an orchestration run, deriving its status from the ticket's own
        state (a BLOCKED ticket yields a BLOCKED run, anything else SUCCEEDED)."""
        cancelled = orchestration_cancel_requested(orch_run.id)
        if cancelled:
            status = OrchestrationRunStatus.CANCELLED
        elif ticket.state == TicketState.BLOCKED:
            status = OrchestrationRunStatus.BLOCKED
        else:
            status = OrchestrationRunStatus.SUCCEEDED
        self.callbacks.complete_orchestration(
            orch_run,
            ticket,
            status=status,
            message="Cancelled by operator" if cancelled else "",
        )
        self.session.refresh(orch_run)
        return orch_run

    def _advance_after_stage(
        self,
        ticket: Ticket,
        profile: OrchestrationProfile,
        stage_def: WorkflowStageDef,
        orch_run: OrchestrationRun,
        target_key: str,
    ) -> OrchestrationRun | None:
        """Post-stage advance: gate checks and routing after a stage ran.

        Returns the orchestration run to hand back to the caller when this
        pass must stop here (awaiting a human, gate-blocked, or no route
        forward), or None when the main loop should continue.
        """
        self.session.refresh(ticket)
        instance, stages = self.orch._resolve_stages(ticket)
        stage_map = parse_stage_map(instance, stages) if instance else {}
        status_after = stage_map.get(target_key, ticket.workflow_stage_status)

        if status_after == StageStatus.AWAITING:
            return self._pause_orchestration(orch_run, ticket, message="Awaiting human approval")

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
                # Stage was routed back to itself for an inline retry; the
                # main loop re-runs it this same pass.
                return None
            # _GateDecision.PASS falls through to advance normally.

        if not next_key:
            return self._pause_orchestration(orch_run, ticket)
        return None

    def _has_child_tickets(self, ticket: Ticket) -> bool:
        return (
            self.session.exec(
                select(Ticket.id).where(Ticket.parent_ticket_id == ticket.id).limit(1)
            ).first()
            is not None
        )

    def _finalize_aggregator_parent(
        self, orch_run: OrchestrationRun, ticket: Ticket
    ) -> OrchestrationRun:
        """Complete a parent ticket without running any of its own stages and close
        the run. Called only once every child is complete; the finalize/mark-done
        decision (including the no-terminal-stage fallback) lives in
        finalize_aggregator_ticket."""
        finalize_aggregator_ticket(self.session, self.orch, ticket)
        self.session.refresh(ticket)
        return self._complete_run(orch_run, ticket)

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

    @staticmethod
    def _should_stop_orchestration(ticket: Ticket, orch_run: OrchestrationRun) -> bool:
        if orchestration_cancel_requested(orch_run.id):
            return True
        if ticket.state in (TicketState.DONE, TicketState.WONT_DO):
            return True
        return ticket.state == TicketState.BLOCKED and not blocked_by_interruption(ticket)

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
        *,
        auto_approve: bool = False,
    ) -> OrchestrationRun | None:
        """Handle a stage with no agent to run (the final `done` stage, or a
        human-approval gate). Returns None if the caller should `continue` the
        loop (workflow just finished, or the gate auto-resolved), else the
        `orch_run` to return now.
        """
        if is_terminal_stage(stage_def):
            self.orch.finalize_workflow(ticket)
            self.session.refresh(ticket)
            return None
        self.orch.enter_human_gate(ticket, stage_key=target_key)
        self.session.refresh(ticket)
        if auto_approve and auto_resolve_awaiting_gate(self.session, ticket, orch_run, target_key):
            return None
        return self._pause_orchestration(orch_run, ticket, message="Awaiting human approval")

    def _dispatch_agent_stage(
        self,
        ticket: Ticket,
        orch_run: OrchestrationRun,
        stage_def: WorkflowStageDef,
        target_key: str,
        *,
        auto_approve: bool,
        timeout_seconds: int | None,
        stop_at_stage_key: str | None,
        resuming: bool,
    ) -> bool:
        """Run a parallel or sequential agent stage; True means stop the loop."""
        if stage_def.stage_type == "parallel":
            return self._run_parallel_stage_or_stop(
                ticket,
                orch_run,
                stage_def,
                target_key,
                auto_approve=auto_approve,
                timeout_seconds=timeout_seconds,
                resuming=resuming,
            )
        return self._run_sequential_stage(
            ticket,
            orch_run,
            target_key,
            auto_approve=auto_approve,
            timeout_seconds=timeout_seconds,
            stop_at_stage_key=stop_at_stage_key,
        )

    def _run_parallel_stage_or_stop(
        self,
        ticket: Ticket,
        orch_run: OrchestrationRun,
        stage_def: WorkflowStageDef,
        target_key: str,
        *,
        auto_approve: bool,
        timeout_seconds: int | None = None,
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
            timeout_seconds=timeout_seconds,
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
        timeout_seconds: int | None = None,
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
            timeout_override_seconds=timeout_seconds,
        )
        with lease_renewal(agent_run.id):
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
            # A scope-denied implementer set a reroute pin and reset this stage to
            # PENDING (see permission_bridge._try_scope_reroute). The run "failed"
            # only because the wrong specialist ran — don't block; let this pass
            # continue so the stage re-dispatches to the sibling the pin names.
            if ticket.scope_reroute_agent:
                return False
            # The boundary check parked this stage on an approval rather than
            # running it (see services.handoff_boundary). Nothing failed and
            # nothing is wrong with the ticket — a human has been asked whether
            # the tree the stage would run on is the one it should. Pause, so the
            # answer arrives at an inbox item instead of a blocked ticket.
            if ticket.workflow_stage_status == StageStatus.AWAITING:
                self._pause_orchestration(orch_run, ticket, message="Awaiting human approval")
                return True
            self.callbacks.block_ticket(
                orch_run,
                ticket,
                stage_key=target_key,
                message=completed.stderr or "Stage sub-agent failed",
            )
            return True

        return False

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
        if not detail:
            # Evaluate the transition gate on *every* advance — including when
            # gates are disabled or nothing runnable is configured — and record
            # the outcome, so a gate that ran-and-passed is auditable apart from
            # one that never ran (ticket 88). run_transition_gates short-circuits
            # cheaply for the disabled/skipped cases; only a real "failed" sets
            # detail and pulls in the recovery path below.
            result = run_transition_gates(
                self.session,
                profile,
                workspace,
                ticket,
                from_stage=from_stage,
                to_stage=to_stage,
                stage_def=stage_def,
            )
            record_gate_evaluation(
                self.session,
                self.callbacks,
                ticket,
                orch_run,
                result,
                from_stage=from_stage,
                to_stage=to_stage,
            )
            if not result.ok:
                if result.outcome is GateOutcome.UNAVAILABLE:
                    # The gate could not run — a hung command, a binary not on
                    # PATH. That is a fact about the machine, and handing it to
                    # the stage's own agent buys a full CLI run that cannot
                    # possibly fix it, then re-runs a stage that already passed.
                    # Straight to a human.
                    self._block_after_gate_failure(
                        ticket, instance, stages, orch_run, from_stage, clean_gate_detail(result)
                    )
                    return _GateDecision.BLOCKED
                detail = clean_gate_detail(result)
        if not detail:
            return _GateDecision.PASS

        # Gate failed. First, let mechanical fixers have a go — these clear the
        # "basic problems" (imports, formatting, trivial lint) with no agent run.
        if profile.gates.autofix_commands:
            autofix = run_gate_autofix(
                self.session,
                profile,
                workspace,
                ticket,
                from_stage=from_stage,
                to_stage=to_stage,
                stage_def=stage_def,
            )
            if autofix.ran:
                residual = run_gates_detail(
                    self.session, ticket, profile, workspace, stage_def, from_stage, to_stage
                )
                if not residual:
                    self._commit_autofix(ticket, from_stage, autofix.output)
                    return _GateDecision.PASS
                detail = residual

        return self._decide_unfixed_gate_failure(
            ticket, instance, stages, orch_run, profile, from_stage, detail
        )

    def _decide_unfixed_gate_failure(
        self,
        ticket: Ticket,
        instance: WorkflowInstance,
        stages: list[WorkflowStageDef],
        orch_run: OrchestrationRun,
        profile: OrchestrationProfile,
        from_stage: str,
        detail: str,
    ) -> _GateDecision:
        """Who should look at a gate failure the fixers could not clear.

        Three answers, in order of how much they cost: nobody on this ticket
        (the failure is not its work), the stage's own agent (bounded retries),
        or a human.
        """
        # A worktree-scoped gate reads the whole tree, so it can fail on a file
        # another ticket left uncommitted beside this one — and rerouting for
        # that asks an agent to fix code it never wrote. Only a confident
        # disagreement diverts: the gate named paths, this ticket recorded
        # paths, they do not overlap, and they are in different parts of the
        # tree. Anything less is UNKNOWN and takes the path below unchanged,
        # because the ticket's own side is empty far more often than not
        # (lg-workflow-integrity-406).
        attribution, gate_paths = attribute_gate_failure(
            gate_output=detail,
            ticket_paths=set(self._ticket_changed_paths(ticket)),
        )
        if attribution is GateFaultAttribution.FOREIGN:
            self._escalate_foreign_gate_failure(
                ticket, instance, stages, orch_run, from_stage, detail, gate_paths
            )
            return _GateDecision.BLOCKED

        # Route back to the stage's own agent with the gate errors in context,
        # up to a bounded number of tries — counted durably (see
        # count_gate_fix_attempts) so the budget can't be refreshed just by
        # starting a new orchestration run.
        attempts = count_gate_fix_attempts(self.session, ticket.id, from_stage)
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
            title=gate_failure_artifact_title(from_stage),
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

    def _escalate_foreign_gate_failure(
        self,
        ticket: Ticket,
        instance: WorkflowInstance,
        stages: list[WorkflowStageDef],
        orch_run: OrchestrationRun,
        from_stage: str,
        detail: str,
        gate_paths: set[str],
    ) -> None:
        """The gate failed on code this ticket did not write.

        Blocks for a human like an exhausted gate does, but says something
        different in the process: the paths that triggered it, and the fact that
        none of them belong to this ticket. Rerouting instead spent a full agent
        turn producing a document explaining the agent could not act, three times
        over on ticket 22 of the blobert milestone 14 run.

        Deliberately not an auto-fix and not a retry. Nothing this ticket's agent
        can do changes a file it does not own, so more attempts cannot converge —
        the only useful next actor is a person who can see the whole tree.
        """
        offending = ", ".join(sorted(gate_paths)) or "(none reported)"
        self.callbacks.attach_artifact(
            ticket,
            kind="error",
            title=gate_failure_artifact_title(from_stage),
            content={
                "message": (
                    f"{detail}\n\n"
                    f"Attribution: none of the paths this gate named belong to "
                    f"{ticket.external_id}. Offending paths: {offending}. This is a fault in "
                    "the surroundings — most often another ticket's uncommitted work in a "
                    "shared tree — so it was not routed to this ticket's agent, which cannot "
                    "fix a file it does not own."
                ),
                "run_code": "",
                "agent_id": "",
                "stage_key": from_stage,
                "command": "",
            },
        )
        summary = (
            f"Transition gate after '{from_stage}' failed on paths outside this ticket: "
            f"{offending}. Needs an operator, not rework."
        )
        record_blocking_issue(
            self.session,
            ticket,
            run_id=None,
            stage_key=from_stage,
            message=summary,
        )
        self.callbacks.block_ticket(orch_run, ticket, stage_key=from_stage, message=summary)

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
            title=gate_failure_artifact_title(from_stage),
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
        timeout_seconds: int | None = None,
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

        runs = self._start_parallel_stage_runs(
            ticket,
            orch_run,
            stage_def,
            stage_key,
            pending_specs,
            auto_approve=auto_approve,
            timeout_seconds=timeout_seconds,
        )
        tree_error = prepare_tree_for_parallel_stage(self.session, ticket, stage_key, runs)
        if tree_error:
            return False, tree_error
        results = _run_and_collect_parallel_results(runs)
        return reconcile_parallel_stage(self.session, ticket, orch_run, stage_key, results)

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
        return [
            spec
            for spec in specs
            if not member_passed(
                latest_member_run(self.session, ticket, stage_def, stage_key, spec)
            )
        ]

    def _start_parallel_stage_runs(
        self,
        ticket: Ticket,
        orch_run: OrchestrationRun,
        stage_def: WorkflowStageDef,
        stage_key: str,
        specs,
        *,
        auto_approve: bool,
        timeout_seconds: int | None = None,
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
                timeout_override_seconds=timeout_seconds,
            )
            runs.append(run)
        return runs

    def _recover_interrupted_stage(self, ticket: Ticket, instance, stages) -> str | None:
        """Clear a stage blocked only by a server restart, not a genuine failure.

        Startup reconciliation marks both the stage and ticket BLOCKED. The execute
        loop admits only exact interruption markers so this method can re-arm that
        stage; genuine failures remain stopped. next_executable_stage() otherwise
        refuses every BLOCKED stage, so Continue Run would silently no-op forever.

        Returns the recovered stage key (so callers can tell _execute_parallel_stage
        this is a resume, not a fresh attempt) or None if nothing needed recovering.
        """
        stage_map = parse_stage_map(instance, stages)
        if not blocked_by_interruption(ticket):
            return None
        stage_key = interrupted_stage_key(self.session, ticket, stage_map)
        if not stage_key:
            return None

        set_stage_status(ticket, instance, stages, stage_key, StageStatus.PENDING)
        ticket.blocking_issues = ""
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        return stage_key


def _orchestrate_incomplete_children(
    builtin: BuiltinOrchestrator,
    ticket: Ticket,
    profile: OrchestrationProfile,
    *,
    auto_approve: bool = False,
    timeout_seconds: int | None = None,
    subtree_budget: SubtreeBudget | None = None,
) -> str | None:
    """Run direct child workflows sequentially before advancing the parent.

    `auto_approve`, `timeout_seconds` and `subtree_budget` propagate into each
    nested execute() call, recursively covering the whole descendant subtree —
    without this, every child run would default back to auto_approve=False and
    the agent's own timeout. Module-level so BuiltinOrchestrator stays under
    its size cap.
    """
    children = list(
        builtin.session.exec(select(Ticket).where(Ticket.parent_ticket_id == ticket.id)).all()
    )
    prereqs = TicketDependencyService(builtin.session).prerequisites_map([c.id for c in children])
    children = order_children_for_subtree(children, prereqs)
    for child in children:
        if child.work_item_type not in WORKFLOW_WORK_ITEM_TYPES:
            continue
        builtin.orch.ensure_workflow_instance(child, commit=True)
        if ticket_workflow_complete(builtin.orch, child):
            continue
        child_run = BuiltinOrchestrator(builtin.session).execute(
            child,
            profile,
            max_stages=None,
            auto_approve=auto_approve,
            timeout_seconds=timeout_seconds,
            _subtree_budget=subtree_budget,
        )
        builtin.session.refresh(ticket)
        builtin.session.refresh(child)
        if child.state == TicketState.BLOCKED:
            return f"Child ticket blocked: {child.title}"
        if child_run.status == OrchestrationRunStatus.BLOCKED:
            return f"Child workflow blocked: {child.title}"
        if not ticket_workflow_complete(builtin.orch, child):
            # Chain the child's own pause reason so a block deeper in the
            # subtree stays visible at every level above it — otherwise a
            # grandparent's run reports only "Child workflow paused" and the
            # blocked grandchild two levels down is invisible from the top.
            reason = (child_run.error_message or "").strip()
            suffix = f" — {reason}" if reason else ""
            return f"Child workflow paused: {child.title}{suffix}"
    return None


def _run_and_collect_parallel_results(runs: list[AgentRun]) -> list[ParallelMemberResult]:
    """Run parallel stage members and judge each one.

    Module-level so ``BuiltinOrchestrator`` stays under its size cap. Executing
    the members is this driver's job; deciding what each result *means* is
    ``services.parallel_stage``'s, so both drivers agree.
    """
    results: list[ParallelMemberResult] = []

    def _run_agent(run_id: str) -> ParallelMemberResult:
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
            return member_result_from_run(completed)

    from sqlmodel.pool import StaticPool

    if isinstance(engine.pool, StaticPool):
        for run in runs:
            try:
                results.append(_run_agent(run.id))
            except Exception as exc:
                results.append(
                    ParallelMemberResult(agent_id=run.agent_id, failure=f"{run.agent_id}: {exc}")
                )
    else:
        with ThreadPoolExecutor(max_workers=max(1, len(runs))) as pool:
            future_map = {pool.submit(_run_agent, run.id): run.agent_id for run in runs}
            for future in as_completed(future_map):
                agent_label = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(
                        ParallelMemberResult(agent_id=agent_label, failure=f"{agent_label}: {exc}")
                    )

    return results
