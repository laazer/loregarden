"""Parallel-stage semantics, shared by every driver that can run one.

A parallel stage fans out to ``stage_def.parallel_agents``: several agents run
the same stage concurrently over the same tree, and the stage settles only once
all of them have. Two drivers do that today — the built-in orchestrator, which
spawns the members itself, and the external-harness protocol, which hands them
out over MCP and is told about each one coming back — and the *semantics* must
not differ between them: same shared worktree, same per-member stage-report
artifact, same rework routing when a member rejects.

So the semantics live here, and the drivers only supply the members' results.
Everything a driver still owns — how a member is executed, and when — stays with
the driver.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    ParallelAgentSpec,
    RunStatus,
    StageStatus,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.git_branch import ensure_ticket_branch
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.rework_feedback import (
    record_reroute_exhausts_budget,
    rework_reroute_count,
)
from loregarden.services.stage_report import (
    StageReport,
    is_transient_failure,
    parse_stage_report,
    stage_report_artifact_content,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.workflow_routing import apply_stage_route, previous_stage_key
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

#: Stage-report statuses that make a member a rejection rather than a pass. The
#: report contract owns this vocabulary (see ``services.stage_report``), which is
#: why it is a set of its literals rather than an enum of ours.
_REJECTING_REPORT_STATUSES = frozenset({"fail", "needs_rework", "blocked"})
_PASSING_REPORT_STATUS = "pass"

#: Artifact kind a stage report is filed under.
_STAGE_REPORT_ARTIFACT_KIND = "context"

#: Transition outcome a rejected parallel stage routes on.
_REJECT_OUTCOME = "reject"


@dataclass(frozen=True)
class ParallelMemberResult:
    """One member's contribution to settling a parallel stage.

    ``failure`` is empty for a member that passed. Fail-closed: a clean exit with
    no parseable stage report is a failure, because a stage nobody reported on
    cannot be said to have passed.
    """

    agent_id: str
    failure: str = ""
    report: StageReport | None = None
    transient: bool = False


def member_result(
    agent_id: str, *, status: RunStatus, stdout: str, stderr: str
) -> ParallelMemberResult:
    """Judge one member from what its run produced."""
    report = parse_stage_report(stdout)
    if status != RunStatus.SUCCEEDED:
        return ParallelMemberResult(
            agent_id=agent_id,
            failure=f"{agent_id}: {stderr or 'agent run failed'}",
            report=report,
            # Infrastructure failures (API/usage limit, an unauthenticated CLI)
            # are not rework rejections; reconciliation pauses for a retry
            # instead of rerouting upstream on them.
            transient=is_transient_failure(stdout, stderr),
        )
    if report is None:
        return ParallelMemberResult(
            agent_id=agent_id,
            failure=f"{agent_id}: missing <<<LOREGARDEN_STAGE_REPORT>>> block",
        )
    if report.status in _REJECTING_REPORT_STATUSES:
        return ParallelMemberResult(
            agent_id=agent_id,
            failure=f"{agent_id}: {report.reroute_context or 'agent reported failure'}",
            report=report,
        )
    return ParallelMemberResult(agent_id=agent_id, report=report)


def member_result_from_run(run: AgentRun) -> ParallelMemberResult:
    """Judge one member from its settled run row."""
    return member_result(
        run.agent_id,
        status=run.status,
        stdout=run.stdout or "",
        stderr=run.stderr or "",
    )


def member_passed(run: AgentRun | None) -> bool:
    """Whether this member is done and does not need running again."""
    if run is None or run.status != RunStatus.SUCCEEDED:
        return False
    report = parse_stage_report(run.stdout or "")
    return report is not None and report.status == _PASSING_REPORT_STATUS


def member_skill_name(stage_def: WorkflowStageDef, spec: ParallelAgentSpec) -> str:
    """The skill a member runs under: its own, else the stage's."""
    return spec.skill_name or stage_def.skill_name


def latest_member_run(
    session: Session,
    ticket: Ticket,
    stage_def: WorkflowStageDef,
    stage_key: str,
    spec: ParallelAgentSpec,
) -> AgentRun | None:
    """This member's most recent run of this stage, or None if it never ran.

    Lanes may share an agent and differ only by skill — three planners under
    different lenses, say. Matching on agent alone would let one finished lane
    answer for its siblings, so the skill is part of the identity.
    """
    return session.exec(
        select(AgentRun)
        .where(
            AgentRun.ticket_id == ticket.id,
            AgentRun.stage_key == stage_key,
            AgentRun.agent_id == spec.agent_id,
            AgentRun.skill_name == member_skill_name(stage_def, spec),
        )
        .order_by(AgentRun.created_at.desc())
    ).first()


def prepare_tree_for_parallel_stage(
    session: Session, ticket: Ticket, stage_key: str, runs: list[AgentRun]
) -> str:
    """Have the tree the members will share ready before any of them start.

    Resolved once, here, rather than by each member: the workers run
    concurrently and would otherwise race to create the ticket's worktree and
    end up in three different trees. Only when the worktree policy is off does
    this fall back to checking the branch out in the shared tree.

    Returns an error message (and finalizes the stage as BLOCKED) on failure,
    else an empty string.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace or not runs:
        return ""

    workspace_root = resolve_workspace_root(workspace)
    if not workspace_root.is_dir():
        return ""

    try:
        if resolve_execution_root(session, runs[0], ticket, workspace) != workspace_root:
            return ""
        ensure_ticket_branch(workspace_root, ticket)
    except (ValueError, subprocess.CalledProcessError) as exc:
        message = f"Failed to checkout branch: {exc}"
        orch = OrchestrationService(session)
        orch.finalize_stage(
            ticket,
            stage_key,
            status=StageStatus.BLOCKED,
            blocking_message=message,
        )
        session.refresh(ticket)
        return message
    return ""


def reconcile_parallel_stage(
    session: Session,
    ticket: Ticket,
    orch_run: OrchestrationRun,
    stage_key: str,
    results: list[ParallelMemberResult],
) -> tuple[bool, str]:
    """Settle a parallel stage from every member's result.

    Files each member's stage report as an artifact, then either finalizes the
    stage DONE or routes the rework its rejections ask for. ``True`` means the
    stage settled cleanly; the message carries the failures otherwise.
    """
    callbacks = OrchestrationCallbackService(session)
    for result in results:
        if result.report is None:
            continue
        callbacks.attach_artifact(
            ticket,
            kind=_STAGE_REPORT_ARTIFACT_KIND,
            title=f"Stage report — {stage_key} ({result.agent_id})",
            content=stage_report_artifact_content(stage_key, result.report),
        )

    failures = [result.failure for result in results if result.failure]
    if failures:
        return _route_parallel_stage_failures(session, ticket, orch_run, stage_key, results)

    orch = OrchestrationService(session)
    orch.finalize_stage(ticket, stage_key, status=StageStatus.DONE)
    session.refresh(ticket)
    return True, ""


def _route_parallel_stage_failures(
    session: Session,
    ticket: Ticket,
    orch_run: OrchestrationRun,
    stage_key: str,
    results: list[ParallelMemberResult],
) -> tuple[bool, str]:
    """Route a rejected parallel stage's rework: record the reviewers' feedback for
    the re-run agent and either reroute upstream or, at the loop cap, block for a
    human. Pairs with run_completion._reroute_or_block_for_rework (single-stage).
    """
    orch = OrchestrationService(session)
    callbacks = OrchestrationCallbackService(session)
    message = "; ".join(result.failure for result in results if result.failure)
    transient = any(result.transient for result in results)
    transitions = orch._resolve_transitions(ticket)

    # Prefer an agent-specified reroute target (highest-confidence among
    # reject/needs_rework reports) over the template's `reject` transition —
    # apply_stage_route falls back to the template route, then to the immediately
    # preceding stage, when this is empty.
    rejecting = [
        result.report
        for result in results
        if result.report
        and result.report.status in _REJECTING_REPORT_STATUSES
        and result.report.reroute_to_stage
    ]
    rejecting.sort(key=lambda report: report.confidence, reverse=True)
    agent_to_key = rejecting[0].reroute_to_stage if rejecting else ""
    agent_context = rejecting[0].reroute_context if rejecting else ""

    if transient and not rejecting:
        # The only failures were infrastructure (API/usage limit, overload, a CLI
        # that could not authenticate), and no reviewer produced a genuine
        # rejection. Rerouting to `implement` would waste a cycle and, via the
        # rework loop cap, inch toward blocking for the wrong reason. Pause the
        # stage for a human/resume instead — no reroute, no ledger entry, so the
        # loop budget is untouched. A genuine rejection from another reviewer
        # (rejecting non-empty) still takes precedence and is rerouted below with
        # its real feedback.
        callbacks.block_ticket(
            orch_run,
            ticket,
            stage_key=stage_key,
            message=(
                f"'{stage_key}' stage hit a transient infrastructure/auth error, not a "
                f"rework rejection. Paused — resume to retry once it clears. ({message[:300]})"
            ),
        )
        session.refresh(ticket)
        return False, message

    template_route = StateMachine.resolve_transition_target(transitions, stage_key, _REJECT_OUTCOME)
    to_key = agent_to_key or (template_route[0] if template_route else "")
    transition_agent = template_route[1] if template_route else ""

    instance, stages = orch._resolve_stages(ticket)
    if instance and stages:
        # Record the reviewers' full feedback for the stage this rework will
        # re-run, so the re-run agent sees every round in full rather than the
        # short pointer ticket.blocking_issues keeps for the UI.
        target_stage = to_key or previous_stage_key(stages, stage_key) or ""
        if record_reroute_exhausts_budget(
            session,
            ticket,
            target_stage=target_stage,
            from_stage=stage_key,
            context=agent_context or message,
        ):
            # Same target rerouted to the loop cap without sticking — stop the
            # loop and pull in a human instead of bouncing again.
            count = rework_reroute_count(session, ticket, target_stage)
            callbacks.block_ticket(
                orch_run,
                ticket,
                stage_key=stage_key,
                message=(
                    f"Rework loop: '{target_stage}' has been rerouted {count}× from "
                    f"'{stage_key}' without passing. Paused for a human — see the "
                    f"accumulated rework feedback before re-running."
                ),
            )
            session.refresh(ticket)
            return False, message

        try:
            apply_stage_route(
                ticket,
                instance,
                stages,
                transitions,
                from_key=stage_key,
                outcome=_REJECT_OUTCOME,
                next_stage_key=to_key,
                next_agent=transition_agent or ticket.next_agent,
                blocking_issues=(agent_context or message)[:2000],
            )
            session.add(ticket)
            session.add(instance)
            session.commit()
            session.refresh(ticket)
            return True, message
        except ValueError:
            pass  # first-in-order stage, nowhere to fall back to — BLOCKED below

    orch.finalize_stage(
        ticket,
        stage_key,
        status=StageStatus.BLOCKED,
        blocking_message=message[:2000],
    )
    session.refresh(ticket)
    return False, message
