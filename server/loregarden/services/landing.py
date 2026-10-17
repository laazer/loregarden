"""What happens to a landing's result when a workflow finishes (lg-milestone-that-768).

`land_ticket` decides whether the merge happened; this module decides what
the workflow does about it. It runs at the moment the workflow is about to
derive `done` — the terminal stage completing with no route onward — and
*before* it does: done → blocked is not a transition the state machine
allows, so a failed landing has to block from in progress.

That moment is in `OrchestrationService`, not in `complete_orchestration`:
the first version hooked the latter and guarded on "not already done", and
the terminal stage's own completion had already written `done` a second
earlier. 717 finished its whole pipeline, reported done, and landed nothing
(lg-milestone-that-777).

Outcomes:

- Landed, or nothing to land: a `TicketLanded` event; the caller derives done.
  A root has nothing to land locally, so its completion publishes instead: a
  tree's root its integration branch (771), a ticket with no tree its own.
- Anything else — a conflict, a ref that moved underneath, git refusing: the
  terminal stage goes BLOCKED and the ticket blocks with the reason, from the
  control plane's own words. The orchestration that drove the stage sees a
  blocked ticket and finishes blocked, as it does for any other block.

Before the merge, the ticket branch is brought up to its target, by merging
the target *into* the branch with the same no-checkout plumbing the land uses
(801). A target that moved after the terminal gate passed is then met on the
ticket branch, which is where a resolver is told to work, and the tree that
lands is the tree the ticket's own gate ran against. A refresh conflict
reaches this module as a `LandResult` with conflicted files, exactly as a land
conflict does, and takes the same resolver path bounded by the same cap. Any
other refresh failure carries git's reason with no conflicted files, so it
blocks on the leg below and spends no resolution attempt.

`auto_resolve_conflicts` is honoured as of 801. A conflict on a ticket whose
every stage passed arms a resolution turn on the terminal stage — the same
pin the repair turn uses — and the workflow comes back through here to retry
the land when it passes. The earlier note said the resolver had to go on the
implement stage, because a run finishing the terminal stage would derive
`done` behind the landing's back; `run_completion` now leaves a terminal stage
PENDING, so the only path to `done` still runs through this module. With the
flag off, or `max_conflict_resolve_attempts` spent, a conflict blocks and the
message says so.
"""

from __future__ import annotations

import logging

from loregarden.agents.registry import REPAIR_AGENT_ID, get_agent
from loregarden.core.event_bus import event_bus
from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_terminal import find_terminal_stage
from loregarden.models.domain import (
    EventType,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.artifact_service import block_ticket_for_unresolved_blocker
from loregarden.services.block_repair import repair_turns_spent
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.land_ticket import LandResult, LandSkip, land_ticket
from loregarden.services.land_verification import LAND_BLOCK_LEAD
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.repair_brief import arm_repair_turn
from loregarden.services.target_branch import subtree_root
from loregarden.services.tree_publish_scheduler import (
    publish_and_settle,
    schedule_tree_publish,
)
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlmodel import Session

logger = logging.getLogger(__name__)


def land_before_done(
    session: Session,
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    *,
    stage_key: str,
) -> bool:
    """Land the ticket's work. True when the caller may go on to derive done.

    False means this function blocked the ticket at ``stage_key`` and the
    workflow must not be reconciled to done.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    if workspace is None:
        # Nothing ran without a workspace; there is no repository to land in.
        return True

    result = land_ticket(session, ticket, workspace)
    _publish(session, ticket, result)
    if result.ok:
        # A root lands nothing locally — its target is the base — but it
        # publishes: a tree's root its integration branch (771), a ticket
        # with no tree its own branch. `publish_tree` is a no-op for anything
        # that is not a root.
        if result.skipped is LandSkip.NO_REPOSITORY:
            return True
        if result.skipped in (LandSkip.NO_BRANCH, LandSkip.BASE_TARGET):
            outcome = publish_and_settle(session, ticket, workspace)
            if outcome.ok:
                return True
            _block(session, ticket, instance, stages, stage_key, _publish_message(outcome))
            return False
        if result.skipped is None:
            # Work just landed on the tree's integration branch: publish the tree
            # now rather than when its root completes (958). Off the landing's
            # thread, since pushing runs the pre-push suite.
            schedule_tree_publish(subtree_root(session, ticket).id, workspace.id)
        return True

    if result.conflicted and _offer_conflict_resolution(
        session, ticket, instance, stages, stage_key=stage_key, result=result, workspace=workspace
    ):
        return False
    _block(session, ticket, instance, stages, stage_key, _block_message(result, workspace, ticket))
    return False


def parked_on_failed_landing(session: Session, ticket: Ticket) -> bool:
    """Whether `ticket` is waiting at its terminal stage on a landing that failed.

    Every stage before it passed; the block is the landing's (or the resolver's
    that followed it). A landing that succeeds by any other route clears exactly
    this, so the caller can finish the workflow instead of leaving a landed
    ticket blocked until someone requeues it (lg-milestone-that-928).
    """
    if ticket.state in StateMachine.TERMINAL_TICKET_STATES:
        return False
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    terminal = find_terminal_stage(stages)
    if instance is None or terminal is None or ticket.workflow_stage_key != terminal.key:
        return False
    return parse_stage_map(instance, stages).get(terminal.key) is StageStatus.BLOCKED


def _offer_conflict_resolution(
    session: Session,
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    *,
    stage_key: str,
    result: LandResult,
    workspace: Workspace,
) -> bool:
    """Arm the terminal stage for a resolution turn. True when it was armed.

    The ticket passed every stage; the only thing between it and done is a
    merge someone has to do. So the resolver is dispatched on the stage the
    ticket is already parked on, under the same pin the repair turn uses, and
    the workflow comes back here to retry the land when it passes.

    Three things make this safe rather than a loop:

    - `auto_resolve_conflicts` must be on (the default). A profile or ticket
      that turns it off gets the block, which says the conflict is a person's.
    - `max_conflict_resolve_attempts` bounds the turns. The operator already
      sets it in the git automation panel, where it governed nothing until
      now; spending it blocks for a person, who requeues to grant more.
    - The run cannot finish the ticket behind the landing's back —
      `run_completion` leaves a terminal stage PENDING, so the only path to
      `done` still runs through `land_before_done`.
    """
    automation = resolve_git_automation(workspace, ticket)
    if not automation.auto_resolve_conflicts:
        return False
    if repair_turns_spent(session, ticket, stage_key) >= automation.max_conflict_resolve_attempts:
        return False
    if get_agent(REPAIR_AGENT_ID) is None:
        # Pinning an agent that cannot resolve would re-arm the stage forever.
        logger.warning(
            "repair agent %r is not registered; landing conflict left for a person",
            REPAIR_AGENT_ID,
        )
        return False

    arm_repair_turn(
        session,
        ticket,
        instance,
        stages,
        _transitions(session, ticket),
        stage_key=stage_key,
        brief=_resolution_brief(result),
    )
    session.add(ticket)
    session.add(instance)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.DISPATCHED_LANDING_RESOLVER,
        stage_key=stage_key,
        reason=(
            f"Landing {result.branch} on {result.target} conflicted in "
            f"{', '.join(result.conflicted_files)}; armed a resolution turn under the "
            f"'{REPAIR_AGENT_ID}' agent instead of blocking a ticket whose every stage passed."
        ),
        evidence={
            "branch": result.branch,
            "target": result.target,
            "conflicted_files": list(result.conflicted_files),
        },
    )
    session.commit()
    logger.warning(
        "Landing %s on %s conflicted in %s; dispatching a resolution turn",
        result.branch,
        result.target,
        ", ".join(result.conflicted_files),
    )
    return True


def _transitions(session: Session, ticket: Ticket) -> list[dict[str, str]]:
    template, _ = resolve_ticket_stages(session, ticket)
    return StateMachine.parse_transitions(template.transitions_json) if template else []


def _resolution_brief(result: LandResult) -> str:
    return (
        f"Every stage passed, but landing '{result.branch}' on '{result.target}' conflicts in "
        f"{', '.join(result.conflicted_files)}. You are its one resolution turn: merge "
        f"'{result.target}' into '{result.branch}' and resolve the conflicts on the ticket "
        "branch, keeping both sides' intent — the other side is another ticket's finished "
        "work, not noise. Commit the resolution and report `pass`; the landing is retried "
        "automatically. If the conflict needs a decision only a person can make, report "
        "`blocked` with that reason instead — there will be no second turn."
    )


def _block(
    session: Session,
    ticket: Ticket,
    instance: WorkflowInstance,
    stages: list[WorkflowStageDef],
    stage_key: str,
    message: str,
) -> None:
    set_stage_status(ticket, instance, stages, stage_key, StageStatus.BLOCKED)
    session.add(instance)
    block_ticket_for_unresolved_blocker(session, ticket, entry=message, stage_key=stage_key)
    logger.warning("Ticket %s did not land: %s", ticket.external_id, message)


def _block_message(result: LandResult, workspace: Workspace, ticket: Ticket) -> str:
    """What the attention row and the reader show for a landing that did not happen.

    The lead comes from `land_verification` rather than being spelled here: it
    is spent out of the same `BLOCKING_ISSUE_INLINE_LIMIT` and the same
    `TEASER_CHARS` as whatever follows it, and a refused verification sizes its
    own words against it. Two copies of the lead would be two answers to "how
    much of the budget is already gone?".
    """
    lead = LAND_BLOCK_LEAD.format(branch=result.branch, target=result.target)
    if result.conflicted:
        files = ", ".join(result.conflicted_files)
        note = ""
        if resolve_git_automation(workspace, ticket).auto_resolve_conflicts:
            note = " (auto_resolve_conflicts is set; its resolution attempts are spent)"
        return (
            f"{lead}merge conflicts in {files}. "
            f"Resolve them on the ticket branch and requeue.{note}"
        )
    return f"{lead}{result.detail}"


def _publish_message(outcome) -> str:
    return f"Could not publish {outcome.branch}: {outcome.detail}"


def _publish(session: Session, ticket: Ticket, result: LandResult) -> None:
    event_bus.publish(
        session,
        EventType.TICKET_LANDED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        payload={
            "ok": result.ok,
            "branch": result.branch,
            "target": result.target,
            "landed_sha": result.landed_sha,
            "skipped": result.skipped.value if result.skipped else "",
            "conflicted_files": list(result.conflicted_files),
            "detail": result.detail,
        },
    )
