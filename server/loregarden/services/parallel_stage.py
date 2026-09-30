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
    BlockKind,
    BlockOrigin,
    OrchestrationRun,
    ParallelAgentSpec,
    ReworkStopReason,
    RunStatus,
    StageStatus,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.block_settlement import settle_block
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
from loregarden.services.target_branch import resolve_target_branch
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

    ``transient`` separates the two kinds of failure that are not rejections: the
    infrastructure ones (an API limit, a CLI that could not authenticate) and the
    protocol one (a clean exit carrying no readable report). Both mean the
    member's verdict is unknown rather than negative, so both pause for a retry
    instead of rerouting the work upstream and spending a round of the loop cap.
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
            # A protocol failure, not a rejection. The member exited cleanly and
            # said nothing this can read, so its verdict is *unknown* — and
            # unknown is not "this work needs rework". Counting it as one used to
            # send the ticket back to `implement` over an envelope the reviewer
            # failed to print, and spend a round of the loop cap doing it: of the
            # four rounds that paused blob-procedural-sdf-36, three were this and
            # a locked database, while every reviewer that did report said pass.
            # Transient routes it to the same retry pause an API limit gets, so
            # the stage still does not pass (fail-closed is unchanged) and the
            # dispatch budget still bounds a member that never reports.
            transient=True,
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
        ensure_ticket_branch(
            workspace_root,
            ticket,
            start_point=resolve_target_branch(session, ticket, workspace, repo_root=workspace_root),
        )
    except (ValueError, subprocess.CalledProcessError) as exc:
        message = f"Failed to checkout branch: {exc}"
        orch = OrchestrationService(session)
        orch.finalize_stage(
            ticket,
            stage_key,
            status=StageStatus.BLOCKED,
            blocking_message=message,
        )
        instance, stages = orch._resolve_stages(ticket)
        # No orchestration run reaches here — this runs before the members are
        # dispatched — so the turn cannot be spent. Settled anyway, so the block
        # carries its kind and says why it got no repair (802).
        settle_block(
            session,
            ticket,
            instance=instance,
            stages=stages,
            stage_key=stage_key,
            message=message,
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
    stage DONE or routes the rework its rejections ask for.

    ``True`` means **the caller need not block the ticket** — not that the stage
    passed. Three things satisfy it: every member passed, the rework was routed
    upstream, or the stage was re-armed for its one repair turn (802). Only the
    first is a verdict; the other two are "this is handled, keep going", and the
    driver's own `_stage_wants_another_attempt` is what re-dispatches. Read it
    as "settled", never as "passed" — a test that asserted `ok is False` to mean
    "nobody reported on this stage" was reading it the second way.
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
    the re-run agent and either reroute upstream, spend the stage's repair turn,
    or, at the loop cap, block for a human. Pairs with
    run_completion._reroute_or_block_for_rework (single-stage).

    Returns the same "no block needed" boolean `reconcile_parallel_stage` does —
    see its docstring for why that is not the same as "passed".
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

    instance, stages = orch._resolve_stages(ticket)

    if transient and not rejecting:
        # The only failures were infrastructure (API/usage limit, overload, a CLI
        # that could not authenticate) or protocol (a clean exit with no readable
        # stage report), and no reviewer produced a genuine rejection. Rerouting
        # to `implement` would waste a cycle and, via the rework loop cap, inch
        # toward blocking for the wrong reason. A genuine rejection from another
        # reviewer (rejecting non-empty) still takes precedence and is rerouted
        # below with its real feedback.
        #
        # This is the likeliest place in the whole workflow to lose a stage
        # report — N lenses, N chances to drop the envelope — and until 802 it
        # was the one shape a repair turn could never reach, so it went straight
        # to a person every time. Offer the turn first; the block still waits
        # for a person when the turn cannot be spent, and says why.
        block_message = (
            f"'{stage_key}' stage hit a transient infrastructure or protocol error, not "
            f"a rework rejection. ({message[:300]})"
        )
        settlement = settle_block(
            session,
            ticket,
            orch_run,
            instance=instance,
            stages=stages,
            transitions=transitions,
            stage_key=stage_key,
            message=block_message,
            # Transient by construction: `is_transient_failure` already judged
            # every member's failure to be infrastructure or protocol, so the
            # message's own wording is not what decides this.
            declared=BlockKind.HARNESS,
        )
        if settlement.repair_armed:
            session.refresh(ticket)
            return True, message
        callbacks.block_ticket(
            orch_run,
            ticket,
            origin=BlockOrigin.CONTROL_PLANE,
            stage_key=stage_key,
            message=f"{block_message} Paused — resume to retry once it clears.",
            settlement=settlement,
        )
        session.refresh(ticket)
        return False, message

    template_route = StateMachine.resolve_transition_target(transitions, stage_key, _REJECT_OUTCOME)
    to_key = agent_to_key or (template_route[0] if template_route else "")
    transition_agent = template_route[1] if template_route else ""

    if instance and stages:
        # Record the reviewers' full feedback for the stage this rework will
        # re-run, so the re-run agent sees every round in full rather than the
        # short pointer ticket.blocking_issues keeps for the UI.
        target_stage = to_key or previous_stage_key(stages, stage_key) or ""
        stop = record_reroute_exhausts_budget(
            session,
            ticket,
            target_stage=target_stage,
            from_stage=stage_key,
            context=agent_context or message,
        )
        if stop is not ReworkStopReason.NONE:
            # Either the loop cap ran out, or two rounds running asked for the
            # same thing against the same tree. The second is worth saying
            # differently: it means re-running cannot help, rather than that we
            # ran out of patience.
            count = rework_reroute_count(session, ticket, target_stage)
            if stop is ReworkStopReason.STUCK:
                stop_message = (
                    f"Rework loop is not converging: '{target_stage}' raised the same "
                    f"finding against the same commit twice running (round {count}). "
                    f"Re-running cannot differ from the last attempt. Paused for a human; "
                    f"the repeated finding is in the rework feedback."
                )
            else:
                stop_message = (
                    f"Rework loop: '{target_stage}' has been rerouted {count}× from "
                    f"'{stage_key}' without passing. Paused for a human — see the "
                    f"accumulated rework feedback before re-running."
                )
            callbacks.pause_for_rework_decision(
                orch_run,
                ticket,
                stage_key=stage_key,
                target_stage=target_stage,
                message=stop_message,
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
                # `transition_agent` alone, with no `or ticket.next_agent`
                # fallback. This argument is a pin WRITE, not a read: an empty
                # hint makes `apply_stage_route` derive the agent from the
                # TARGET stage (workflow_routing.py:323-327), which is the right
                # answer. Feeding it the ticket's existing pin re-pinned a value
                # dispatch may already have cleared, and on this path — a
                # parallel stage with N reviewers — it could pin a single
                # reviewer as the rework target, which is exactly what the
                # derivation above exists to prevent.
                next_agent=transition_agent,
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
    # Nowhere upstream to send the rework, so the stage itself is what has to
    # be re-run — which is exactly what a repair turn does (802).
    settlement = settle_block(
        session,
        ticket,
        orch_run,
        instance=instance,
        stages=stages,
        transitions=transitions,
        stage_key=stage_key,
        message=message[:2000],
    )
    session.refresh(ticket)
    return settlement.repair_armed, message
