"""Run a whole ticket from a coding harness outside this control plane.

An operator copies a ticket prompt out of the UI into their own Claude Code or
Codex session. That session then drives the ticket over MCP: it opens an
orchestration run, checks each stage out, does the work, and hands the stage
report back. Loregarden records the same things it records for its own runs —
stage transitions, artifacts, start and finish times — plus *which* harness did
it, so an outside run is comparable against a control-plane one.

Two rules separate this path from the built-in driver:

- **No queue.** The lane pool exists to bound how many agents this machine
  spawns. An external harness spawns nothing here, so reserving a lane for it
  would idle a slot that could be running real work — and make the operator wait
  on capacity this run does not consume.
- **No supervision.** There is no child process to orphan, so these runs are
  exempt from the restart reapers (see ``fail_interrupted_runs``).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from loregarden.agents.mcp_context import resolve_mcp_url
from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_terminal import find_terminal_stage
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    ExternalHarnessPromptView,
    ExternalStageResultView,
    ExternalStageRunView,
    ExternalStageView,
    OrchestrationDriver,
    OrchestrationRun,
    RunStatus,
    RunUsage,
    RunUsageStatus,
    StageStatus,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.block_repair import repair_pinned
from loregarden.services.dependency_hold import DependencyHold, refuse_held_dispatch
from loregarden.services.handoff_settle import (
    settle_failed_handoff,
    settle_failed_handoff_run,
)
from loregarden.services.handoff_tree_status import (
    HandoffTreePreparationError,
    describe_handoff_failure,
    describe_handoff_tree,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import (
    ProfileUnreadableError,
    resolve_orchestration_profile,
)
from loregarden.services.oversized_ticket_split import split_oversized_ticket
from loregarden.services.parallel_stage import (
    latest_member_run,
    member_passed,
    member_runs_and_results,
    member_skill_name,
    prepare_tree_for_parallel_stage,
    reconcile_parallel_stage,
)
from loregarden.services.review_relens import (
    decide_lenses,
    record_relens_decisions,
    skipped_members,
)
from loregarden.services.run_interruption import SUPERSEDED_RUN_MESSAGE
from loregarden.services.run_lease import agent_run_lease_expired
from loregarden.services.run_service import RunService, fail_interrupted_runs
from loregarden.services.stage_retry_budget import (
    refund_stage_dispatch_charged_since,
    stage_dispatch_marker_ids,
)
from loregarden.services.stage_shape import describe_stage_shape
from loregarden.services.studio_routing import is_parallel_stage, ticket_stage_definition
from loregarden.services.subtree_auto_run import (
    finalize_aggregator_ticket,
    order_children_for_subtree,
    ticket_workflow_complete,
)
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Marks an agent run that a harness outside this process executed. Mirrors
#: ``TERMINAL_HANDOFF_COMMAND_PREFIX``: there is no argv to record, so the
#: command field carries what ran it instead of a lie about a subprocess.
EXTERNAL_HARNESS_COMMAND_PREFIX = "[external-harness]"

HARNESS_LABELS: dict[ExternalHarness, str] = {
    ExternalHarness.CLAUDE_CODE: "Claude Code",
    ExternalHarness.CODEX: "Codex",
    ExternalHarness.CURSOR: "Cursor",
    ExternalHarness.OTHER: "an external coding harness",
}

_GATE_MESSAGE = (
    "This stage runs no agent — it is a human approval gate. Loregarden has "
    "opened it in the approval inbox; stop here and tell the operator."
)
_FINISHED_MESSAGE = (
    "The workflow has no stage left to run — it reached its terminal stage. "
    "Finish by calling loregarden_complete_orchestration."
)
_BLOCKED_MESSAGE = (
    "A stage on this ticket is blocked, so there is nothing to check out. Report "
    "the blocker to the operator, or clear it and ask for the stage by key."
)
_SETTLED_MESSAGE = (
    "Every member of this stage had already been handed back, so the stage has "
    "now been settled. Ask for the next stage."
)


def _workspace_for(session: Session, ticket: Ticket) -> Workspace:
    workspace = workspace_for_ticket(session, ticket)
    if not workspace:
        raise ValueError(f"Unknown workspace for ticket: {ticket.id}")
    return workspace


def _workflow_finished(session: Session, ticket: Ticket) -> bool:
    """Whether there is nothing left for the harness to run.

    Answered from the stage map, not from ``ticket.state``. Finalizing a
    workflow writes both, but the ticket row can be held back independently of
    the stages — a state lock taken mid-run leaves every stage DONE with the
    ticket still ``in_progress`` — and a finished workflow reported as a pending
    approval gate stalls an autonomous harness on an inbox item that will never
    appear. The terminal stage being resolved is the fact this question is
    actually about.

    Only a ticket with no terminal stage at all falls back to ticket state;
    there is nothing else left to ask.
    """
    _, stages = resolve_ticket_stages(session, ticket)
    terminal = find_terminal_stage(stages)
    if terminal is None:
        return ticket.state in StateMachine.TERMINAL_TICKET_STATES
    status = OrchestrationService(session).stage_status(ticket, terminal.key)
    return status in (StageStatus.DONE, StageStatus.WONT_DO)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def start_external_orchestration(
    session: Session, ticket: Ticket, *, harness: ExternalHarness
) -> OrchestrationRun:
    """Open an orchestration run driven by ``harness``, without taking a lane."""
    workspace = _workspace_for(session, ticket)
    profile = resolve_orchestration_profile(workspace)
    return OrchestrationCallbackService(session).start_orchestration_run(
        ticket,
        driver=OrchestrationDriver.EXTERNAL_MCP,
        profile_slug=profile.slug,
        external_harness=harness,
    )


def _stage_name(session: Session, ticket: Ticket, stage_key: str) -> str:
    views = OrchestrationService(session).build_stage_views(ticket)
    view = next((s for s in views if s.key == stage_key), None)
    return view.name if view else stage_key


def _checked_out_run_view(
    session: Session, orch_run: OrchestrationRun, run: AgentRun, ticket: Ticket
) -> ExternalStageRunView:
    """Stamp a started run as this harness's, and render its prompt.

    Raises ``ValueError`` carrying the sentence the harness reads, which is all
    it gets — the MCP boundary turns this into an ``isError`` tool result and
    there is no run view behind it. For a *tree-preparation* failure that is
    ``describe_handoff_failure``'s sentence; for anything else it is the
    failure's own diagnosis, because the tree sentence replaced rather than
    padded it (lg-milestone-that-975). Settling the run this stamped is the
    **caller's**, because the three call sites want three different answers: a
    fresh single-agent checkout settles the one run it stamped, a fan-out
    settles every member, and a resumed run is not settled at all
    (lg-milestone-that-974 R7).
    """
    run_svc = RunService(session)
    run.orchestration_run_id = orch_run.id
    run.external_harness = orch_run.external_harness
    session.add(run)
    session.commit()

    try:
        prompt, tree = run_svc.executor.render_stage_prompt(run, ticket)
    # Subclass first: `HandoffTreePreparationError` is a `ValueError`, so a
    # broader arm above it swallows every tree failure. Both arms re-raise a
    # *plain* `ValueError` because the three call sites below catch `ValueError`
    # and settle the run they stamped on it — nothing else ever settles an
    # external run.
    except HandoffTreePreparationError as exc:
        # The tree really is what failed, so the tree sentence is the answer,
        # with git's own output behind it.
        raise ValueError(describe_handoff_failure(session, ticket, exc)) from exc
    # `ValueError` is named literally, and the guard is no broader than these
    # two. `ProfileUnreadableError` stays named now that the renderer wraps it:
    # it is a `RuntimeError`, and the name is the enumeration contract from
    # `orchestration_profile.py:44-51` rather than a reachability claim
    # (lg-milestone-that-1215 S7).
    except (ProfileUnreadableError, ValueError) as exc:
        # Unclassified, so it keeps its own diagnosis as the lead sentence: the
        # text is all the harness gets, and a sentence about a tree the failure
        # is not about replaces the diagnosis instead of prefacing it.
        raise ValueError(str(exc)) from exc

    # Both facts about the tree come from the tree the render prepared. The
    # boundary used to resolve the target a second time, one line below the
    # handler above and catching nothing, so a conflict there escaped as a bare
    # `RuntimeError` with none of this sentence (lg-milestone-that-974).
    status = describe_handoff_tree(tree, ticket_external_id=ticket.external_id)
    if status.warning:
        logger.warning("External handoff for %s: %s", ticket.external_id, status.warning)
        prompt = f"{prompt}\n\n## The tree you were given\n\n{status.warning}\n"

    run.command = f"{EXTERNAL_HARNESS_COMMAND_PREFIX} {orch_run.external_harness.value}"
    session.add(run)
    session.commit()
    session.refresh(run)
    return ExternalStageRunView(
        agent_run_id=run.id,
        run_code=run.run_code,
        agent_id=run.agent_id,
        skill_name=run.skill_name,
        prompt=prompt,
        repo_path=str(tree.repo_root),
        target_branch=tree.target_branch,
        tree_warning=status.warning,
        started_at=_as_utc(run.started_at),
    )


def _resumable_stage_run(session: Session, ticket: Ticket, stage_key: str) -> AgentRun | None:
    """The run a single-agent stage is still holding, or None if it holds none.

    The single-agent counterpart of ``latest_member_run``, with two conditions
    the member lookup gets for free from its member identity.

    **Only an externally-harnessed run may be adopted.** Checking a run out
    stamps ``external_harness`` on it (``_checked_out_run_view``), and that stamp
    is not cosmetic: it flips ``run_has_renewer`` to False, so
    ``settle_expired_agent_runs`` stops judging the run, and it is what the
    unscoped restart sweep exempts. Writing it onto a run this control plane
    supervises would hand the harness a run the built-in driver is still
    working, and remove the only two things that would ever settle it. A run
    left RUNNING by an in-process driver is that driver's to finish or the boot
    sweep's to reap — never this path's to adopt.

    **Liveness is the codebase's existing question**, ``agent_run_lease_expired``:
    a recorded pid that is gone settles it outright. For an external run with no
    pid the answer is always "alive", which is the fail-closed policy that
    predicate exists to apply — a harness reports at stage boundaries only, so
    silence is not evidence.

    **RUNNING only.** ``AWAITING_PERMISSION`` is deliberately absent rather than
    an unreachable arm: a permission pause writes ``StageStatus.AWAITING`` at the
    same moment it writes that run status (``permission_bridge``), so the
    caller's ``StageStatus.RUNNING`` gate never reaches this query for a paused
    run. It could not match one anyway — the permission bridge belongs to the
    in-process CLI adapter, and a run with no subprocess here never enters it.

    **Exactly one, or none.** A single-agent stage has one run; two in flight is
    residue, and there is no fact in the rows that says which one the harness
    means — a newest-wins pick with equal timestamps is arbitrary, and the loser
    would be left RUNNING with nothing that ever settles an external run. So
    ambiguity is not resolved here: the caller reaps and starts fresh, which is
    what this path did before resume existed.
    """
    candidates = session.exec(
        select(AgentRun).where(
            AgentRun.ticket_id == ticket.id,
            AgentRun.stage_key == stage_key,
            AgentRun.status == RunStatus.RUNNING,
            # Triage turns are a side channel, not workflow runs — see run_service.fail_interrupted_runs for why (602).
            AgentRun.agent_id != TRIAGE_AGENT_ID,
            col(AgentRun.external_harness).is_not(None),
        )
    ).all()
    # Declining here is safe only because `run_service.fail_interrupted_runs`,
    # ticket-scoped, selects a SUPERSET of everything this could have adopted —
    # the caller reaps when this returns None. That relationship is the design,
    # not drift, and it is asserted by
    # `test_the_reaper_selects_a_superset_of_what_resume_can_adopt`
    # (lg-workflow-integrity-602).
    live = [run for run in candidates if not agent_run_lease_expired(session, run)]
    return live[0] if len(live) == 1 else None


def _begin_parallel_stage(
    session: Session,
    orch_run: OrchestrationRun,
    ticket: Ticket,
    stage_def: WorkflowStageDef,
    stage_key: str,
) -> ExternalStageView:
    """Check every member of a parallel stage out at once.

    A parallel stage has no single agent — the members live in
    ``stage_def.parallel_agents`` and fanning them out is the driver's job, which
    on this path is the harness's. It gets one runnable entry per member and runs
    them however it likes; the stage settles only when the last one is handed
    back to ``finish_external_stage``.

    Checking the same stage out twice is a re-checkout, not a second attempt: a
    member already in flight is re-served rather than duplicated, and a member
    that already passed is not offered again.
    """
    orch = OrchestrationService(session)
    resuming = orch.stage_status(ticket, stage_key) == StageStatus.RUNNING
    if not resuming:
        # A fresh attempt supersedes whoever last held this stage. When resuming
        # we must not reap: the members still in flight are the ones being
        # re-served, and this reaper claims external runs when ticket-scoped.
        fail_interrupted_runs(
            session,
            ticket_id=ticket.id,
            stage_key=stage_key,
            message=SUPERSEDED_RUN_MESSAGE,
        )

    # Same rule as the builtin driver, so a stage does not re-review three lenses
    # here and one there purely because of who dispatched it
    # (lg-workflow-integrity-499).
    decisions = [] if resuming else decide_lenses(session, ticket, stage_def, stage_key)
    if decisions:
        record_relens_decisions(session, ticket, stage_def, stage_key, decisions)
    skipped = skipped_members(stage_def, decisions)

    # Read before the loop opens anything, so the one marker this pass goes on to
    # charge is the one id missing from the snapshot. Read it after the first
    # `start_run` and the set difference is empty on every path, which makes the
    # refund below a silent no-op (lg-milestone-that-1213 AC9).
    markers_before = stage_dispatch_marker_ids(session, ticket.id, stage_key)

    runs: list[AgentRun] = []
    # The subset of `runs` this handoff stamped RUNNING itself. An adopted member
    # (a RUNNING `latest` re-served below) is work the harness is already
    # holding, and settling it is exactly what `_resumable_stage_run` exists to
    # prevent — so a render failure may only reap what this pass opened
    # (lg-milestone-that-974 AC13/AC14).
    stamped: list[AgentRun] = []
    for spec in stage_def.parallel_agents:
        if (spec.agent_id, member_skill_name(stage_def, spec)) in skipped:
            continue
        latest = (
            latest_member_run(session, ticket, stage_def, stage_key, spec) if resuming else None
        )
        if latest is not None and member_passed(latest):
            continue
        if latest is not None and latest.status == RunStatus.RUNNING:
            runs.append(latest)
            continue
        opened = orch.start_run(
            ticket,
            stage_key=stage_key,
            orchestration_run_id=orch_run.id,
            agent_id=spec.agent_id,
            skill_name=member_skill_name(stage_def, spec),
        )
        runs.append(opened)
        stamped.append(opened)
    session.refresh(ticket)

    orch_run.current_stage_key = stage_key
    session.add(orch_run)
    session.commit()

    if not runs:
        # Every member had already been settled while the stage sat RUNNING.
        # Reconciling is what finalizes it; returning an empty checkout without
        # doing so would leave the harness asking for this stage forever.
        _, results = member_runs_and_results(session, ticket, stage_def, stage_key)
        reconcile_parallel_stage(session, ticket, orch_run, stage_key, results)
        session.refresh(ticket)
        return ExternalStageView(
            stage_key=stage_key,
            stage_name=_stage_name(session, ticket, stage_key),
            parallel=True,
            message=_SETTLED_MESSAGE,
        )

    # One tree for every member, resolved before any of them starts — they run
    # concurrently and would otherwise race to create the ticket's worktree.
    tree_error = prepare_tree_for_parallel_stage(session, ticket, stage_key, runs, stamped=stamped)
    if tree_error:
        session.refresh(ticket)
        return ExternalStageView(
            stage_key=stage_key,
            stage_name=_stage_name(session, ticket, stage_key),
            parallel=True,
            message=tree_error,
        )

    try:
        views = [_checked_out_run_view(session, orch_run, run, ticket) for run in runs]
    except ValueError as exc:
        # The fan-out stamps one run per member, each committed as it goes, so a
        # failure on member k leaves 1..k-1 RUNNING with nothing behind them and
        # nothing that ever settles an external run. It settles all of them, and
        # reports through the same `message` shape this path already uses for a
        # tree it could not prepare — a harness meets one convention here, not
        # two (lg-milestone-that-974 R7/AC13).
        #
        # `stamped`, not `runs`: a resuming fan-out's `runs` also holds members
        # adopted from a prior pass, and those stay RUNNING (AC14). When every
        # member was adopted, nothing is settled and the stage stays RUNNING,
        # which is correct — the harness still holds that work.
        detail = str(exc)
        return _settle_fanned_out_members(
            session, ticket, stamped, stage_key, detail=detail, markers_before=markers_before
        )

    return ExternalStageView(
        stage_key=stage_key,
        stage_name=_stage_name(session, ticket, stage_key),
        parallel=True,
        runs=views,
    )


def _settle_fanned_out_members(
    session: Session,
    ticket: Ticket,
    runs: list[AgentRun],
    stage_key: str,
    *,
    detail: str,
    markers_before: frozenset[str],
) -> ExternalStageView:
    """Fail every member this fan-out left running, and report the failure once.

    ``runs`` must be only the members **this** pass stamped RUNNING, never a
    resuming fan-out's full list: an adopted member is work the harness is still
    holding, and failing it destroys that work unrecoverably, since nothing else
    ever settles an external run.

    The dispatch refund is per **pass**, which is why this settles with
    ``settle_failed_handoff_run`` (the per-run primitive) rather than with the
    refunding composition. What charges an external fan-out is
    ``evaluate_standalone_stage_dispatch`` inside ``orch.start_run``, with
    members 2..N grouped out by ``dispatch_pass_open`` — so the pass is charged
    one marker for N members, or none at all when that grouping is already open
    or the dispatch is reroute-exempt. Handing back one marker per member
    deleted N-1 *earlier* passes' charges and reset the stage retry breaker
    (lg-milestone-that-1213). One refund, keyed to the marker ids read before
    this pass opened anything, returns exactly what it charged.
    """
    run_svc = RunService(session)
    for member in runs:
        session.refresh(member)
        if member.status == RunStatus.RUNNING:
            settle_failed_handoff_run(session, run_svc, member, ticket, detail=detail)
    refund_stage_dispatch_charged_since(
        session, ticket.id, stage_key, markers_before=markers_before
    )
    session.refresh(ticket)
    return ExternalStageView(
        stage_key=stage_key,
        stage_name=_stage_name(session, ticket, stage_key),
        parallel=True,
        message=detail,
    )


def _aggregator_view(session: Session, ticket: Ticket, stage_key: str) -> ExternalStageView | None:
    """The builtin driver's aggregator rule, for a ticket an external harness drives.

    An oversized ticket is split here exactly as the builtin driver splits it,
    and a ticket with children never serves its own stages: the children carry
    the work, and the parent finalizes once they are all complete. None when
    the ticket has no children and its stages are the harness's to run.
    """
    split_oversized_ticket(session, ticket, OrchestrationCallbackService(session))
    children = list(session.exec(select(Ticket).where(Ticket.parent_ticket_id == ticket.id)).all())
    if not children:
        return None
    orch = OrchestrationService(session)
    prereqs = TicketDependencyService(session).prerequisites_map([c.id for c in children])
    incomplete = []
    for child in order_children_for_subtree(children, prereqs):
        # A child with no instance yet reads complete to the check below.
        orch.ensure_workflow_instance(child, commit=True)
        if not ticket_workflow_complete(orch, child):
            incomplete.append(child)
    if not incomplete:
        finalize_aggregator_ticket(session, orch, ticket)
        session.refresh(ticket)
        return ExternalStageView(stage_key=ticket.workflow_stage_key, message=_FINISHED_MESSAGE)
    order = ", ".join(child.external_id for child in incomplete)
    return ExternalStageView(
        stage_key=stage_key,
        message=(
            f"{ticket.external_id} has child tickets, which carry its work, so it runs "
            "none of its own stages. Finish this orchestration with "
            "loregarden_complete_orchestration, then drive each child with "
            f"loregarden_start_orchestration in this order: {order}. Once they are "
            "all complete, an orchestration started on this ticket finalizes it."
        ),
    )


def begin_external_stage(
    session: Session, orch_run: OrchestrationRun, *, stage_key: str | None = None
) -> ExternalStageView:
    """Check the next stage out to the harness driving ``orch_run``.

    Returns the prompt Loregarden's own agent would have been given for that
    stage, so the harness runs the same instructions rather than an improvised
    reading of the ticket. A parallel stage returns one entry per member; every
    other stage returns exactly one. Every view carries ``stage_shape`` — what
    the stage is, in words — so a harness reading ``runs`` cannot mistake a
    parallel stage for a human gate (745).
    """
    ticket = session.get(Ticket, orch_run.ticket_id)
    if not ticket:
        raise ValueError(f"Ticket not found: {orch_run.ticket_id}")
    view = _begin_external_stage(session, orch_run, ticket, stage_key=stage_key)
    stage = ticket_stage_definition(session, ticket, view.stage_key) if view.stage_key else None
    if stage is not None:
        view.stage_shape = describe_stage_shape(session, ticket, stage)
    return view


def _begin_external_stage(
    session: Session, orch_run: OrchestrationRun, ticket: Ticket, *, stage_key: str | None
) -> ExternalStageView:
    if orch_run.external_harness is None:
        raise ValueError(
            "This orchestration run was not opened by an external harness — "
            "start one with loregarden_start_orchestration and external_harness set."
        )

    # Checking a stage out is this harness saying it is alive, and it is one of
    # only two things it ever says. Nothing else stamps `last_seen_at` on this
    # path — the lease is renewed by the `start_stage` / `complete_stage`
    # callbacks, which the external protocol does not use — so without this the
    # run's newest liveness evidence is `started_at`, and a harness working
    # steadily past the lease looks exactly like one whose operator closed the
    # terminal an hour ago.
    OrchestrationCallbackService(session).touch_lease(orch_run)

    # Pick the stage the same way the builtin driver does. Settling a stage
    # leaves ticket.workflow_stage_key on the stage that just finished — choosing
    # the next one is the driver's job — so falling through to the cursor here
    # would hand the harness the stage it had just reported on, forever.
    target_key = stage_key or OrchestrationService(session).next_executable_stage_key(ticket)
    if not target_key:
        return ExternalStageView(
            stage_key=ticket.workflow_stage_key,
            message=_FINISHED_MESSAGE if _workflow_finished(session, ticket) else _BLOCKED_MESSAGE,
        )

    try:
        # Before the reap below, which would otherwise fail a run it then has
        # no stage to replace.
        refuse_held_dispatch(session, ticket, stage_key=target_key)
    except DependencyHold as hold:
        return ExternalStageView(stage_key=target_key, message=str(hold))

    parent_view = _aggregator_view(session, ticket, target_key)
    if parent_view is not None:
        return parent_view

    orch = OrchestrationService(session)
    stage_def = orch.stage_definition(ticket, target_key)
    # A repair-pinned parallel stage is served as one run under the repair
    # agent, not as a fan-out — the same rule the builtin driver applies, so the
    # two drivers do not differ on it. Fanning out would re-dispatch the members
    # and leave the pin unconsumed (802).
    if (
        stage_def is not None
        and is_parallel_stage(stage_def)
        and not repair_pinned(ticket, target_key)
    ):
        return _begin_parallel_stage(session, orch_run, ticket, stage_def, target_key)

    # The same re-checkout rule the parallel path applies, for a stage that can
    # only have one run: a stage still RUNNING is being re-served, and reaping
    # its run would kill the work the harness is asking about and hand back a
    # second run for a single-agent stage.
    resumed = (
        _resumable_stage_run(session, ticket, target_key)
        if orch.stage_status(ticket, target_key) == StageStatus.RUNNING
        else None
    )
    if resumed is not None:
        orch_run.current_stage_key = target_key
        session.add(orch_run)
        session.commit()
        return ExternalStageView(
            stage_key=target_key,
            stage_name=_stage_name(session, ticket, target_key),
            runs=[_checked_out_run_view(session, orch_run, resumed, ticket)],
        )

    # Nothing to re-serve, so this is a fresh attempt and it supersedes whoever
    # last held the stage — including a run left in flight under a stage still
    # reading RUNNING, which is exactly the state that has no other path to
    # settlement. The reap inside `start_run_async` would claim the same runs —
    # it is ticket and stage scoped, which deliberately claims external runs —
    # but blame them on a server reload that never happened. Claiming them here
    # first says what actually took them; nothing else ever settles an external
    # run.
    fail_interrupted_runs(
        session,
        ticket_id=ticket.id,
        stage_key=target_key,
        message=SUPERSEDED_RUN_MESSAGE,
    )

    run_svc = RunService(session)
    run = run_svc.start_stage_execution(ticket, stage_key=target_key)
    session.refresh(ticket)
    if run is None:
        # An agentless stage: a human approval gate, or the terminal stage,
        # which start_stage_execution has already finalized.
        return ExternalStageView(
            stage_key=target_key,
            message=_FINISHED_MESSAGE if _workflow_finished(session, ticket) else _GATE_MESSAGE,
        )

    orch_run.current_stage_key = run.stage_key
    session.add(orch_run)
    session.commit()
    try:
        view = _checked_out_run_view(session, orch_run, run, ticket)
    except ValueError as exc:
        # This handoff stamped the run RUNNING and committed it one call ago,
        # and `start_stage_execution` charged the stage's dispatch budget one
        # before that. Nothing else ever settles an external run, so a failed
        # checkout that left it would strand the stage live with no agent behind
        # it (lg-milestone-that-974 AC12). The resumed arm above deliberately
        # does **not** do this: it did not stamp the run it was handed.
        settle_failed_handoff(session, run_svc, run, ticket, detail=str(exc))
        raise

    return ExternalStageView(
        stage_key=run.stage_key,
        stage_name=_stage_name(session, ticket, run.stage_key),
        runs=[view],
    )


def _record_external_usage(
    session: Session,
    run: AgentRun,
    *,
    usage: RunUsage | None,
    changed_paths: list[str] | None,
) -> None:
    """Write the harness's self-report onto the run, before it is completed.

    Committed here rather than folded into ``complete_run`` so the numbers
    survive even if routing the stage afterwards raises — the same reason
    ``complete_run`` commits the run's terminal status before touching the
    ticket.

    Only what was reported is written. A harness that sends nothing leaves the
    columns NULL, and a harness that reports usage but no paths keeps the
    ``[]`` it was created with, which already means "nothing recorded here".
    """
    if usage is not None:
        run.input_tokens = usage.input_tokens
        run.output_tokens = usage.output_tokens
        run.cache_read_tokens = usage.cache_read_tokens
        run.cache_write_tokens = usage.cache_write_tokens
        run.model = usage.model or None
        run.effort = usage.effort or None
        # A harness that reported is measured; one that sent an empty block
        # tried and had nothing, which is not the same as never having a usage
        # surface (lg-workflow-integrity-496).
        run.usage_status = (
            RunUsageStatus.MEASURED if usage.measured() else RunUsageStatus.UNAVAILABLE
        )
    if changed_paths:
        run.changed_paths_json = json.dumps(sorted(set(changed_paths)))
        # Marked here too: a value without its marker reads as "no record",
        # and this is the other writer of that column.
        run.changed_paths_recorded_at = datetime.now(timezone.utc)
    if usage is not None or changed_paths:
        session.add(run)
        session.commit()


def finish_external_stage(
    session: Session,
    run: AgentRun,
    *,
    transcript: str,
    failed: bool = False,
    usage: RunUsage | None = None,
    changed_paths: list[str] | None = None,
) -> ExternalStageResultView:
    """Settle a stage the harness has finished, routing on its stage report.

    ``transcript`` goes through the same parser a supervised run's stdout does,
    so a `<<<LOREGARDEN_STAGE_REPORT>>>` block from an outside harness reroutes,
    blocks or advances the workflow identically.

    ``usage`` and ``changed_paths`` are the two things only the harness can
    know. There is no subprocess here, so nothing on this side read a usage
    event or diffed the tree before and after — which is why every externally
    driven run to date has no changed paths recorded at all. Both stay unset
    when the harness does not report them, because an unmeasured run must not
    read as a free one.

    On a parallel stage this settles **one member**. The stage itself is left
    RUNNING until the last outstanding member is handed back, at which point it
    is reconciled by the same code the built-in driver reconciles with.
    """
    if run.external_harness is None:
        raise ValueError(f"Run {run.run_code} was not checked out to an external harness")

    _record_external_usage(session, run, usage=usage, changed_paths=changed_paths)

    # The other half of the renewal. Settling the stage ends the agent run whose
    # liveness was covering this orchestration, and the harness is about to ask
    # for the next stage — so the gap between the two is precisely where a sweep
    # would otherwise reclaim the lane from a harness that is still working.
    orch_run = (
        session.get(OrchestrationRun, run.orchestration_run_id)
        if run.orchestration_run_id
        else None
    )
    if orch_run is not None:
        OrchestrationCallbackService(session).touch_lease(orch_run)

    ticket = session.get(Ticket, run.ticket_id)
    if not ticket:
        raise ValueError(f"Ticket not found: {run.ticket_id}")

    orch = OrchestrationService(session)
    stage_def = orch.stage_definition(ticket, run.stage_key)
    parallel = stage_def is not None and is_parallel_stage(stage_def)

    started_at = _as_utc(run.started_at) or _as_utc(run.created_at)
    run = orch.complete_run(
        run,
        status=RunStatus.FAILED if failed else RunStatus.SUCCEEDED,
        stdout=transcript,
        # One member's report must not route the whole stage. The stage settles
        # once, below, from every member's report together.
        advance_workflow=not parallel,
    )
    session.refresh(run)
    session.refresh(ticket)

    outstanding = 0
    stage_finalized = True
    if parallel:
        outstanding, results = member_runs_and_results(session, ticket, stage_def, run.stage_key)
        stage_finalized = outstanding == 0
        if stage_finalized:
            if orch_run is None:
                raise ValueError(
                    f"Run {run.run_code} has no orchestration run, so its parallel "
                    "stage cannot be settled — re-check the stage out."
                )
            reconcile_parallel_stage(session, ticket, orch_run, run.stage_key, results)
            session.refresh(ticket)

    finished_at = _as_utc(run.finished_at) or datetime.now(timezone.utc)
    duration = (finished_at - started_at).total_seconds() if started_at else 0.0
    return ExternalStageResultView(
        agent_run_id=run.id,
        stage_key=run.stage_key,
        status=run.status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(duration, 3),
        workflow_stage_key=ticket.workflow_stage_key,
        workflow_stage_status=ticket.workflow_stage_status,
        ticket_state=ticket.state,
        blocking_issues=ticket.blocking_issues,
        workflow_finished=_workflow_finished(session, ticket),
        stage_finalized=stage_finalized,
        outstanding_members=outstanding,
    )


def build_external_harness_prompt(
    session: Session, ticket: Ticket, *, harness: ExternalHarness
) -> ExternalHarnessPromptView:
    """The text an operator pastes into ``harness`` to run this ticket there."""
    workspace = _workspace_for(session, ticket)
    orch = OrchestrationService(session)
    stages = orch.build_stage_views(ticket)
    template = orch.get_template_for_ticket(ticket)
    criteria = json.loads(ticket.acceptance_criteria_json or "[]")
    label = HARNESS_LABELS[harness]

    lines = [
        f"# Loregarden ticket handoff — {ticket.external_id}: {ticket.title}",
        "",
        f"You are running this ticket in **{label}**, outside Loregarden's own",
        "orchestrator. Loregarden is the control plane: it owns the ticket, the workflow",
        "stages, and the run record. You drive the pipeline; it records what happened.",
        "",
        "## Ticket",
        "",
        f"- workspace_slug: `{workspace.slug}`",
        f"- external_id: `{ticket.external_id}`",
        f"- ticket_id: `{ticket.id}`",
        f"- repo path: `{resolve_workspace_root(workspace)}`",
        f"- workflow: `{template.slug if template else '—'}`",
        f"- current stage: `{ticket.workflow_stage_key or '—'}`",
        f"- state: `{ticket.state.value}`",
        "",
        "There is no ticket markdown file anywhere in the repo. Everything below comes",
        "from Loregarden's database, and every update goes back the same way.",
        "",
        "### Description",
        "",
        ticket.description or "_(empty — read the ticket over MCP before assuming a scope)_",
        "",
        "### Acceptance criteria",
        "",
        *([f"- {item}" for item in criteria] or ["_(none recorded)_"]),
        "",
        "### Workflow stages",
        "",
        "| # | key | name | agent | status |",
        "| - | --- | ---- | ----- | ------ |",
        *[
            f"| {i + 1} | `{s.key}` | {s.name} | {s.agent_id or '—'} | {s.status.value} |"
            for i, s in enumerate(stages)
        ],
        "",
        "## Connect to Loregarden",
        "",
        f"Loregarden's MCP server is at `{resolve_mcp_url()}` (HTTP). Add it before you start:",
        "",
        "```bash",
        f"claude mcp add --transport http loregarden {resolve_mcp_url()}",
        "```",
        "",
        "In Claude Code the tools are named `mcp__loregarden__<tool>`; other harnesses use",
        "the bare names below. Do not call the MCP endpoint with curl or hand-rolled",
        "JSON-RPC — use your harness's own MCP client.",
        "",
        "## The loop you must run",
        "",
        "**1. Open the run.**",
        "",
        "```",
        "loregarden_start_orchestration",
        f'  ticket_id="{ticket.id}"',
        f'  external_harness="{harness.value}"',
        "```",
        "",
        "Keep the returned `run_id`. Passing `external_harness` is what marks every stage,",
        "status change and timing on this ticket as yours rather than the control plane's —",
        "without it the comparison this run exists for is impossible. It also takes this run",
        "**out of the queue entirely**: you do not wait for a lane, and you must not poll one.",
        "",
        "**2. Check out a stage.**",
        "",
        "```",
        "loregarden_begin_external_stage",
        '  run_id="<run_id from step 1>"',
        "```",
        "",
        "You get back `stage_key` and a `runs` list. Each entry has its own `agent_run_id`,",
        "`agent_id`, `skill_name`, `repo_path` and `prompt` — the exact instruction set",
        "Loregarden's own agent would receive. Follow it, in `repo_path`, instead of",
        "improvising from the ticket text above. An empty `runs` list means the stage runs",
        "nothing; `message` says why, and you stop.",
        "",
        "**3. Do the stage's work.**",
        "",
        "A `parallel` stage returns several entries. They are separate reviewers of the same",
        "change, not steps: run each one against its own prompt — concurrently if you can —",
        "and keep their outputs apart. Every entry shares one `repo_path`, resolved before",
        "any of them started, so do not create a tree of your own.",
        "",
        "**4. Hand each run back.**",
        "",
        "```",
        "loregarden_finish_external_stage",
        '  agent_run_id="<one agent_run_id from step 2>"',
        '  transcript="<that run\'s stage report, verbatim>"',
        "```",
        "",
        "`transcript` must contain the `<<<LOREGARDEN_STAGE_REPORT>>>` block the stage prompt",
        "asks for, unedited. Loregarden parses it and routes the workflow exactly as it does",
        "for its own runs — a rejected report reroutes upstream, a blocked one blocks the",
        "ticket. Call this once per entry in `runs`. A parallel stage stays open until its",
        "last member is back: `stage_finalized` is false and `outstanding_members` counts the",
        "rest. The reply also tells you the run's `duration_seconds`, the next stage, and",
        "whether the workflow is finished.",
        "",
        "**5. Repeat 2–4** until the reply says `workflow_finished`, or you are blocked.",
        "",
        "**6. Close the run.**",
        "",
        "```",
        "loregarden_complete_orchestration",
        '  run_id="<run_id from step 1>"',
        '  status="succeeded"   # or failed / blocked',
        "```",
        "",
        "This is what stamps the ticket's finish time. Do not skip it: an orchestration left",
        "open has no completion time, and a run with no completion time cannot be compared",
        "against anything.",
        "",
        "## Rules",
        "",
        "- Do not call `loregarden_start_stage` or `loregarden_complete_stage` directly. The",
        "  two `external_*` tools wrap them and also open, time and settle the agent run —",
        "  calling the raw pair leaves the stage with no run behind it.",
        "- Do not start a second orchestration for this ticket while one is open.",
        "- Timing is measured server-side, from these calls. Do not report your own elapsed",
        "  time; make the calls at the moments they describe and the numbers follow.",
        "- Record assumptions with `loregarden_append_checkpoint` rather than stopping to ask,",
        "  and call `loregarden_block_ticket` if the work is genuinely blocked.",
        "- Never write a report, summary or findings `.md` file. Long output goes to",
        "  `loregarden_attach_artifact`.",
    ]
    return ExternalHarnessPromptView(
        harness=harness,
        ticket_id=ticket.id,
        external_id=ticket.external_id,
        workspace_slug=workspace.slug,
        prompt="\n".join(lines),
    )
