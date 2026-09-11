"""What happens after a transition gate fails.

Lifted out of `BuiltinOrchestrator`, which sat at exactly its 1000-line cap and
had blocked three consecutive changes on that — the third being
lg-workflow-integrity-683, which needed three lines to record what fixed a gate.
The seam is real rather than convenient: every method here answers one question,
"the gate said no, now what", and none of them needs the orchestrator's executor
or its stage loop. Only the session, the callbacks and the orchestration service.

The recovery has two tiers, in order. Mechanical fixers (ruff --fix, formatters)
run with no agent, and if they clear it the fix is committed and the stage
advances. Otherwise the residual failure goes back to the stage's own agent for a
bounded number of tries, counted durably so a fresh orchestration run cannot
refresh the budget. Only when both are exhausted does a human get pulled in.
"""

from __future__ import annotations

import json
from enum import Enum

from loregarden.agents.registry import DEBUGGER_AGENT_ID
from loregarden.models.domain import (
    AgentRun,
    ArtifactKind,
    GateFaultAttribution,
    GateFixTier,
    GateOutcome,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.artifact_service import looks_like_test_output
from loregarden.services.evidence import has_evidence, resolve_head_sha
from loregarden.services.gate_attribution import (
    GatePartition,
    gate_could_not_examine,
    partition_gate_output,
)
from loregarden.services.gate_observability import (
    clean_gate_detail,
    record_gate_evaluation,
    run_and_record_gates,
)
from loregarden.services.gate_runner import run_gate_autofix, run_transition_gates
from loregarden.services.git_commit_push_service import commit_paths
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.stage_retry_budget import (
    count_gate_fix_attempts,
    foreign_gate_failure_artifact_title,
    gate_failure_artifact_title,
)
from loregarden.services.studio_routing import took_light_route
from loregarden.services.workflow_routing import apply_stage_route
from sqlmodel import Session, select


class GateDecision(Enum):
    """Outcome of the transition-gate check for a completed stage."""

    PASS = "pass"  # gate is clean (or was auto-fixed clean) — advance normally
    REROUTED = "rerouted"  # rerouted back to the stage for an inline auto-fix retry
    BLOCKED = "blocked"  # automatic fixes exhausted — rerouted and paused for a human


class GateRecovery:
    """The gate-failure half of the builtin orchestrator.

    Constructed with the same session, callbacks and orchestration service the
    orchestrator itself holds, so the moved methods keep working unchanged —
    every one of them reached for exactly those three.
    """

    def __init__(
        self,
        session: Session,
        callbacks: OrchestrationCallbackService,
        orch: OrchestrationService,
    ) -> None:
        self.session = session
        self.callbacks = callbacks
        self.orch = orch

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

    def run_gates_with_autofix(
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
    ) -> GateDecision:
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
            return GateDecision.PASS

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
            # A stage that has already been rerouted for a gate fix is being
            # re-evaluated after an agent had a go at it. `count_gate_fix_attempts`
            # is the durable record of that, so the attribution costs no new
            # state (lg-workflow-integrity-683).
            record_gate_evaluation(
                self.session,
                self.callbacks,
                ticket,
                orch_run,
                result,
                from_stage=from_stage,
                to_stage=to_stage,
                fix_tier=(
                    GateFixTier.AGENT
                    if count_gate_fix_attempts(self.session, ticket.id, from_stage)
                    else GateFixTier.NONE
                ),
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
                    return GateDecision.BLOCKED
                detail = clean_gate_detail(result)
        if not detail:
            return GateDecision.PASS

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
                # Recorded, not just re-checked: a fixer clearing a gate used to
                # produce no event at all, so the log read "failed" and stopped
                # (lg-workflow-integrity-683).
                residual = run_and_record_gates(
                    self.session,
                    self.callbacks,
                    ticket,
                    orch_run,
                    profile,
                    workspace,
                    stage_def,
                    from_stage=from_stage,
                    to_stage=to_stage,
                    fix_tier=GateFixTier.MECHANICAL,
                )
                if not residual:
                    self._commit_autofix(ticket, from_stage, autofix.output)
                    return GateDecision.PASS
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
    ) -> GateDecision:
        """Who should look at a gate failure the fixers could not clear.

        Four answers, in order of how much they cost: nobody on this ticket
        (the failure is not its work), a human immediately (the gate could not
        run), the stage's own agent (bounded retries), or a human at the end.
        """
        # A gate that graded nothing has not found a defect, so there is nothing
        # for an agent to fix. Rerouting one spends a bounded retry handing the
        # stage's agent a message that says "fix these issues" and then names
        # none, and no turn can converge on the causes — they are environmental
        # (a worktree left `core.bare`, an unborn HEAD, a base ref that no
        # longer resolves). Straight to a human, before the attribution split
        # below, which reads paths this output does not carry.
        if gate_could_not_examine(detail):
            self._block_after_gate_failure(
                ticket,
                instance,
                stages,
                orch_run,
                from_stage,
                detail,
                blocking_summary=(
                    f"The transition gate at '{from_stage}' could not run, so nothing was "
                    "checked — this is not a finding in the ticket's code. Repair the "
                    "environment it reported (see the Errors tab), then requeue the stage."
                ),
            )
            return GateDecision.BLOCKED

        # A worktree-scoped gate reads the whole tree, so it can fail on a file
        # another ticket left uncommitted beside this one — and rerouting for
        # that asks an agent to fix code it never wrote. Only a confident
        # disagreement diverts: the gate named paths, this ticket recorded
        # paths, they do not overlap, and they are in different parts of the
        # tree. Anything less is UNKNOWN and takes the path below unchanged,
        # because the ticket's own side is empty far more often than not
        # (lg-workflow-integrity-406).
        partition = partition_gate_output(
            gate_output=detail,
            ticket_paths=set(self._ticket_changed_paths(ticket)),
        )
        if partition.attribution is GateFaultAttribution.FOREIGN:
            # Record it loudly and let the ticket through. Blocking here was the
            # first answer (lg-workflow-integrity-452) and it was too narrow a
            # reading: FOREIGN means the violation is somewhere this ticket is
            # not working at all, so it is not one ticket that stalls but every
            # ticket crossing this gate until somebody cleans up. Charging a
            # blameless ticket for another's uncommitted file is the same defect
            # as the reroute this replaced, only quieter (-404).
            self._record_foreign_gate_failure(ticket, from_stage, detail, partition)
            return GateDecision.PASS

        if partition.is_mixed:
            # Some of this failure is the ticket's and some is not. Remediate on
            # its own share only — an agent handed a foreign violation alongside
            # its own spends the turn explaining it cannot act on one of them.
            self._record_foreign_gate_failure(
                ticket, from_stage, partition.foreign_detail, partition
            )
            detail = partition.in_scope_detail or detail

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
            return GateDecision.REROUTED

        # Out of automatic options — reroute for rework and pause for a human.
        self._block_after_gate_failure(ticket, instance, stages, orch_run, from_stage, detail)
        return GateDecision.BLOCKED

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
                kind=ArtifactKind.CONTEXT,
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
            kind=ArtifactKind.ERROR,
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

    def _record_foreign_gate_failure(
        self,
        ticket: Ticket,
        from_stage: str,
        detail: str,
        partition: GatePartition,
    ) -> None:
        """Write down a gate failure this ticket did not cause, and move on.

        Nothing this ticket's agent can do changes a file it does not own, so
        rerouting cannot converge — that was the finding behind
        lg-workflow-integrity-452, and it still holds. What changed is the
        conclusion drawn from it. Blocking for a human made the debt somebody's
        problem immediately, but it made it *every* passing ticket's problem:
        one uncommitted file in a shared tree stops the queue.

        So the debt is recorded as an error artifact naming the offending files
        and the exact command that found them, and the ticket advances. The
        finding stays visible without a blameless ticket paying for it.
        """
        offending = ", ".join(sorted(partition.foreign_paths)) or "(none reported)"
        share = (
            "Some of the paths this gate named belong to other work"
            if partition.is_mixed
            else f"None of the paths this gate named belong to {ticket.external_id}"
        )
        self.callbacks.attach_artifact(
            ticket,
            kind=ArtifactKind.ERROR,
            title=foreign_gate_failure_artifact_title(from_stage),
            content={
                "message": (
                    f"{detail}\n\n"
                    f"Attribution: {share}. Offending paths: {offending}. This is a fault in "
                    "the surroundings — most often another ticket's uncommitted work in a "
                    "shared tree — so it was not routed to this ticket's agent, which cannot "
                    "fix a file it does not own, and it did not block this ticket. It remains "
                    "real: re-run the command below against a clean tree to clear it."
                ),
                "run_code": "",
                "agent_id": "",
                "stage_key": from_stage,
                "command": partition.command,
            },
        )

    def _block_after_gate_failure(
        self,
        ticket: Ticket,
        instance: WorkflowInstance,
        stages: list[WorkflowStageDef],
        orch_run: OrchestrationRun,
        from_stage: str,
        detail: str,
        blocking_summary: str = "",
    ) -> None:
        """Automatic fixes are exhausted. Reroute back to the stage (self-redo)
        and pause for a human — the pre-existing gate-failure behaviour. The raw
        gate output goes to the Errors tab; blocking_issues, rendered directly in
        the workflow pane, stays a short pointer rather than a wall of text.

        `blocking_summary` overrides that pointer for a caller whose failure is
        not the one this text describes. The gate that could not run is the case
        it was added for: "the gate failed" reads as "your code is wrong", and
        the operator would go looking for a violation that was never found.
        """
        self.callbacks.attach_artifact(
            ticket,
            kind=ArtifactKind.ERROR,
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
                blocking_summary
                or f"Transition gate failed at '{from_stage}' — see the Errors tab for details."
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
