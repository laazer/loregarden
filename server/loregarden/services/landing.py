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
  A tree's root has nothing of its own to land, so its completion publishes
  its tree instead (771).
- Anything else — a conflict, a ref that moved underneath, git refusing: the
  terminal stage goes BLOCKED and the ticket blocks with the reason, from the
  control plane's own words. The orchestration that drove the stage sees a
  blocked ticket and finishes blocked, as it does for any other block.

`auto_resolve_conflicts` is not honoured here yet: the resolver has to be
dispatched on the implement stage, not the terminal one, and that routing is
not designed. A conflict blocks and the message says so.
"""

from __future__ import annotations

import logging

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    EventType,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.artifact_service import block_ticket_for_unresolved_blocker
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.land_ticket import LandResult, LandSkip, land_ticket
from loregarden.services.publish_tree import publish_tree
from loregarden.services.workflow_state import set_stage_status
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
        # A tree's root has nothing of its own to land — no branch, or a
        # target that is the base — but its tree does (771). `publish_tree`
        # is a no-op for anything that is not a root.
        if result.skipped is LandSkip.NO_REPOSITORY:
            return True
        if result.skipped in (LandSkip.NO_BRANCH, LandSkip.BASE_TARGET):
            outcome = publish_tree(session, ticket, workspace)
            if outcome.ok:
                return True
            _block(session, ticket, instance, stages, stage_key, _publish_message(outcome))
            return False
        return True

    _block(session, ticket, instance, stages, stage_key, _block_message(result, workspace, ticket))
    return False


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
    if result.conflicted:
        files = ", ".join(result.conflicted_files)
        note = ""
        if resolve_git_automation(workspace, ticket).auto_resolve_conflicts:
            note = " (auto_resolve_conflicts is set, but resolution at landing is not wired yet)"
        return (
            f"Could not land {result.branch} on {result.target}: merge conflicts in "
            f"{files}. Resolve them on the ticket branch and requeue.{note}"
        )
    return f"Could not land {result.branch} on {result.target}: {result.detail}"


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
