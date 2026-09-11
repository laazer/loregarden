"""Builtin orchestrator driver — top-level run invoking stage sub-agents via CLI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.core.state_machine import StateMachine
from loregarden.db.session import engine
from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    AgentRun,
    BlockOrigin,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.gate_recovery import GateDecision, GateRecovery
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
from loregarden.services.review_relens import decide_lenses, record_relens_decisions
from loregarden.services.run_cancellation import orchestration_cancel_requested
from loregarden.services.run_interruption import blocked_by_interruption, interrupted_stage_key
from loregarden.services.run_lease import lease_renewal
from loregarden.services.stage_docker_capacity import stage_docker_capacity
from loregarden.services.stage_retry_budget import (
    enforce_stage_retry_budget,
)
from loregarden.services.stage_transient_retry import stage_rearmed_for_latest_run
from loregarden.services.studio_routing import (
    is_agentless_stage,
    is_terminal_stage,
)
from loregarden.services.subtree_auto_run import (
    SubtreeBudget,
    auto_resolve_awaiting_gate,
    finalize_aggregator_ticket,
    order_children_for_subtree,
    ticket_workflow_complete,
)
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.workflow_state import (
    next_executable_stage,
    parse_stage_map,
    set_stage_status,
)
from sqlmodel import Session, select

#: Per-stage floors, calibrated 2026-09-11 against the 95th percentile of every
#: SUCCEEDED run's wall clock, per stage, with the sample size beside each. The
#: first version of this map (686) was sized by judgement; these are measured.
#:
#: Read the numbers with one caveat, which is why each floor sits *above* its
#: p95 rather than at it: the sample is right-censored. Every run in it completed
#: under the ceiling in force at the time — mostly 600s — so runs that needed
#: longer are absent by construction, and the true distribution has a heavier
#: tail than this one can show. A floor set exactly at a censored p95 would be
#: too small by an unknown amount. Rounding up to the next 300s step is the
#: margin for that, not padding.
#:
#: Raising a floor costs something real: `TIMEOUT_HARD_CAP_MULTIPLIER` is 4, so a
#: genuinely hung stage now burns four times the number below before it is
#: killed. That is the trade this map makes deliberately — a hang is rare and
#: visible, while a stage killed mid-work parks a ticket and looks like a defect
#: in the agent.
STAGE_TIMEOUT_BUDGETS: dict[str, int] = {
    # p95 2559s over 149 runs — the heaviest stage by a wide margin, and the one
    # whose old 600s ceiling produced most of the recorded timeout deaths.
    "implement": 2700,
    # No measured sample of their own (the split implementers are newer than the
    # runs here); they inherit `implement`'s shape, one scope narrower.
    "backend-impl": 1800,
    "frontend-impl": 1800,
    # p95 1968s over 15 runs. Small sample, wide spread — a consultation reads
    # far more than it writes.
    "domain_consultation": 2100,
    # p95 1811s over 8 runs. The smallest sample in this map; treat as
    # provisional and re-measure once it has run more.
    "branch-triage": 2100,
    "verify": 1800,  # p95 1633s over 46 runs
    "test-design": 1800,  # p95 1660s over 70 runs
    "gate": 1800,  # p95 1622s over 23 runs
    "test-break": 1500,  # p95 1343s over 75 runs
    "review": 1200,  # p95 1159s over 114 runs
    "plan": 1200,  # p95 997s over 113 runs
    "script_review": 900,  # p95 859s over 125 runs
    "learning": 900,  # p95 687s over 12 runs
    "spec": 900,  # p95 641s over 59 runs
    "triage": 900,  # p95 627s over 136 runs — p50 is 88s; the tail is the cost
}


def stage_timeout_seconds(stage_def: WorkflowStageDef, run_default: int | None) -> int | None:
    """This stage's agent budget: its own if it declares one, else a floor for
    the heavy stages, else the run's.

    One budget for every stage is why `implement` timed out at 600s while still
    producing output — 35% of `implement` runs that DID succeed ran longer than
    that, against 6% for `triage` (lg-workflow-integrity-686). The spread is
    structural: the stages differ in how much work they are, not in how lucky
    they got.

    Resolved at dispatch rather than written into templates by a migration.
    Every template in this installation that a run actually uses is
    operator-authored (`built_in=0`), so a migration wide enough to help them
    would rewrite operator data and bump a version pins refer to — which is what
    `test_skill_migration_preserves_existing_non_skill_data` exists to catch.
    A default costs no rows and covers templates authored after it lands.

    The budget resizes a bound, never introduces one: a run with no timeout
    keeps none, and a run whose own default is already larger keeps that.
    """
    if stage_def.timeout_seconds:
        return stage_def.timeout_seconds
    if run_default is None:
        return None
    return max(run_default, STAGE_TIMEOUT_BUDGETS.get(stage_def.key, 0))


class BuiltinOrchestrator:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.callbacks = OrchestrationCallbackService(session)
        self.orch = OrchestrationService(session)
        self.gates = GateRecovery(session, self.callbacks, self.orch)
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
                    timeout_seconds=stage_timeout_seconds(stage_def, agent_timeout),
                    stop_at_stage_key=stop_at_stage_key,
                    resuming=(target_key == recovered_stage_key),
                )
                stages_run += 1
                budget.consume(terminal=target_is_terminal)
                if stopped:
                    self.session.refresh(orch_run)
                    return orch_run

                if self._stage_wants_another_attempt(ticket, target_key):
                    continue

                advanced = self._advance_after_stage(
                    ticket, profile, stage_def, orch_run, target_key
                )
                if advanced is not None:
                    return advanced

            self._complete_run(orch_run, ticket)
        except Exception as exc:  # noqa: BLE001 - run boundary; the ticket is blocked with this message
            self.callbacks.block_ticket(
                orch_run,
                ticket,
                # An exception this driver caught at the run boundary.
                origin=BlockOrigin.CONTROL_PLANE,
                message=str(exc),
            )
        self.session.refresh(orch_run)
        return orch_run

    def _stage_wants_another_attempt(self, ticket: Ticket, target_key: str) -> bool:
        """Whether this pass should re-dispatch the stage it just ran.

        Two things re-arm a stage mid-pass, and both mean the same thing to this
        loop: the stage is PENDING again and the work it stands for has not been
        judged, so advancing past it — or running its exit gate — would decide an
        outcome nobody produced.

        **A scope-denial reroute** pinned the sibling implementer
        (`permission_bridge._try_scope_reroute`); the run "failed" only because
        the wrong specialist ran. A parallel stage never sets the pin, so its
        flow is untouched.

        **A transient retry** (`run_completion`, via `stage_transient_retry`):
        the run died of infrastructure, so the exit gate would judge a stage that
        never attempted its work and could block the ticket on the very failure
        the retry is about to clear. Bounded by
        `retry_budget.max_transient_retries`, so this cannot spin.

        One predicate rather than two adjacent guards because `execute` is at its
        statement cap, and because the answer they compute is one question.
        """
        if ticket.scope_reroute_agent:
            return True
        return stage_rearmed_for_latest_run(self.session, ticket, target_key)

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
            message=self._completion_message(status, ticket, cancelled=cancelled),
        )
        self.session.refresh(orch_run)
        return orch_run

    @staticmethod
    def _completion_message(
        status: OrchestrationRunStatus, ticket: Ticket, *, cancelled: bool
    ) -> str:
        """Why this run ended, recorded while the answer is still reachable.

        This path derives BLOCKED from the ticket's state and used to pass "",
        so 29 of 74 blocked runs carry no reason at all
        (lg-workflow-integrity-90). The reason usually existed at the time —
        `blocking_issues` holds it — but it is cleared when the ticket resumes,
        so a message not copied here is not recoverable later. That is the whole
        defect: the data was available exactly once and nobody wrote it down.

        A blocked run with nothing on the ticket still gets a sentence naming the
        stage. It is thin, but it distinguishes "we looked and the ticket said
        nothing" from "nobody recorded anything", which an empty string cannot.
        """
        if cancelled:
            return "Cancelled by operator"
        if status is not OrchestrationRunStatus.BLOCKED:
            return ""
        reason = (ticket.blocking_issues or "").strip()
        if reason:
            return reason
        stage = ticket.workflow_stage_key or "unknown stage"
        return (
            f"Run ended blocked at '{stage}' with no reason recorded on the ticket. "
            "See the ticket's Errors tab for the stage's own output."
        )

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
            decision = self.gates.run_gates_with_autofix(
                ticket,
                profile,
                stage_def,
                instance,
                stages,
                orch_run,
                from_stage=target_key,
                to_stage=next_key,
            )
            if decision is GateDecision.BLOCKED:
                self.session.refresh(orch_run)
                return orch_run
            if decision is GateDecision.REROUTED:
                # Stage was routed back to itself for an inline retry; the
                # main loop re-runs it this same pass.
                return None
            # GateDecision.PASS falls through to advance normally.

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
            # The members' own failure text, joined — a reviewer asking for a
            # person is an agent asking, so the handover check still applies.
            origin=BlockOrigin.AGENT,
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
        with stage_docker_capacity(self.session, agent_run), lease_renewal(agent_run.id):
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
            # `run_completion` judged this failure transient and re-armed the
            # stage. Blocking here would overwrite a PENDING stage with the
            # stderr of a run that never reached the work — the ticket parked on
            # a server reload, which is what the retry exists to stop. Let the
            # pass continue; the loop re-dispatches.
            if stage_rearmed_for_latest_run(self.session, ticket, target_key):
                return False
            self.callbacks.block_ticket(
                orch_run,
                ticket,
                # The stage sub-agent's own stderr.
                origin=BlockOrigin.AGENT,
                stage_key=target_key,
                message=completed.stderr or "Stage sub-agent failed",
            )
            return True

        return False

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

        # A fresh start of a stage that ran before is a RE-review, measured at
        # ~300k tokens a round. `decide_lenses` re-runs the rejecting lens, any
        # lens whose reads the rework touched, and anything it cannot be sure of.
        decisions = [] if resuming else decide_lenses(self.session, ticket, stage_def, stage_key)
        pending_specs = (
            self._incomplete_parallel_specs(ticket, stage_def, stage_key, specs)
            if resuming
            else [decision.spec for decision in decisions if decision.rerun]
        )
        if decisions:
            record_relens_decisions(self.session, ticket, stage_def, stage_key, decisions)
        if not pending_specs:
            # Every member already succeeded before a crash (resuming), or every
            # lens passed and the rework touched nothing it read (re-review).
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
    parked: list[str] = []
    for child in children:
        if child.work_item_type not in WORKFLOW_WORK_ITEM_TYPES:
            continue
        if child.state == TicketState.PARKED:
            # Owed by a person, and deliberately not holding the subtree up.
            # Collected rather than ignored: the parent must still not report
            # itself complete (see the return below), which is what separates
            # parking from WONT_DO (lg-workflow-integrity-449).
            parked.append(child.title)
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
    if parked:
        # Every runnable sibling has now had its turn — this is reported after
        # the loop, not on encountering the first parked child, because the
        # point of parking is that the rest of the subtree keeps moving. The
        # parent stays incomplete because the work is still owed.
        return f"Child ticket parked, awaiting a person: {', '.join(parked)}"
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
            # Renewed for the same reason the single-agent stage at
            # `_run_sequential_stage` is: without it `last_seen_at` is never
            # stamped, `agent_run_lease_expired` falls back to `started_at`,
            # and the reconciliation sweep reaps a review lens that is still
            # working the moment it outruns AGENT_RUN_LEASE. `run_has_renewer`
            # is True for these runs (no external harness), so the fail-closed
            # branch does not cover them.
            with lease_renewal(run.id):
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
            except Exception as exc:  # noqa: BLE001 - member boundary; recorded as a ParallelMemberResult failure
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
                except Exception as exc:  # noqa: BLE001 - future.result() re-raises the worker's error; recorded below
                    results.append(
                        ParallelMemberResult(agent_id=agent_label, failure=f"{agent_label}: {exc}")
                    )

    return results
