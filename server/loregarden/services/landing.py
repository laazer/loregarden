"""What happens to a landing's result at the terminal stage (lg-milestone-that-768).

`land_ticket` decides whether the merge happened; this module decides what
the orchestration does about it. Kept out of `orchestration_callbacks` — the
hook there is three lines — and out of `land_ticket`, which knows git and
nothing about runs, resolvers or blocks.

Three outcomes:

- Landed (or nothing to land): a `TicketLanded` event, and the caller goes on
  to mark the ticket done.
- Conflicted, with `auto_resolve_conflicts` on and a resolver dispatched: the
  conflict is materialised in the ticket's worktree — the resolver works on a
  checkout — and handed over. The orchestration finishes *failed*, naming the
  resolver run: the ticket is not done, but nobody is owed anything either.
  The resolver's run advances the workflow, and the next completion re-lands.
- Anything else — a conflict nobody can take, a ref that moved underneath, git
  refusing outright: the ticket blocks with the reason, from the control
  plane's own words. A ticket that passed every stage and did not land is not
  done, and this is where that stops looking like it was.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Protocol

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    AgentRun,
    BlockOrigin,
    EventType,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.conflict_resolution import request_agent_resolution
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_subprocess import run_git
from loregarden.services.land_ticket import LandResult, land_ticket
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


class Handover(str, Enum):
    """What came of trying to hand a landing conflict to the resolver."""

    #: The resolver is on it; the orchestration finishes failed, not blocked.
    DISPATCHED = "dispatched"
    #: Merging the target into the worktree produced no conflict after all —
    #: the branch has moved since the landing was tried. Land again.
    MERGED_CLEAN = "merged_clean"
    #: No tree, no run, or the resolver was refused. Block.
    REFUSED = "refused"


class LandingCallbacks(Protocol):
    """The two things a landing can do to an orchestration."""

    def block_ticket(
        self,
        orch_run: OrchestrationRun,
        ticket: Ticket,
        *,
        origin: BlockOrigin,
        stage_key: str = "",
        message: str,
    ) -> Ticket: ...

    def _finish_orchestration_run(
        self, orch_run: OrchestrationRun, *, status: OrchestrationRunStatus, message: str = ""
    ) -> None: ...


def land_at_completion(
    session: Session,
    callbacks: LandingCallbacks,
    orch_run: OrchestrationRun,
    ticket: Ticket,
) -> bool:
    """Land the ticket's work. True when the caller may go on to mark it done.

    False means this function already finished the orchestration run — blocked,
    or failed with a resolver on the way — and the ticket must not be reconciled
    to done.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    if workspace is None:
        # Nothing ran without a workspace; there is no repository to land in.
        return True

    result = land_ticket(session, ticket, workspace)
    _publish(session, ticket, result)
    if result.ok:
        return True

    config = resolve_git_automation(workspace, ticket)
    if result.conflicted and config.auto_resolve_conflicts:
        handover = _hand_to_resolver(session, ticket, workspace, result, config)
        if handover is Handover.DISPATCHED:
            callbacks._finish_orchestration_run(
                orch_run,
                status=OrchestrationRunStatus.FAILED,
                message=(
                    f"Landing {result.branch} on {result.target} conflicted in "
                    f"{len(result.conflicted_files)} file(s); resolver dispatched on stage "
                    f"{ticket.workflow_stage_key}"
                ),
            )
            return False
        if handover is Handover.MERGED_CLEAN:
            result = land_ticket(session, ticket, workspace)
            _publish(session, ticket, result)
            if result.ok:
                return True

    callbacks.block_ticket(
        orch_run,
        ticket,
        origin=BlockOrigin.CONTROL_PLANE,
        message=_block_message(result),
    )
    return False


def _block_message(result: LandResult) -> str:
    if result.conflicted:
        files = ", ".join(result.conflicted_files)
        return (
            f"Could not land {result.branch} on {result.target}: merge conflicts in "
            f"{files}. Resolve them on the ticket branch and requeue."
        )
    return f"Could not land {result.branch} on {result.target}: {result.detail}"


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


def _hand_to_resolver(
    session: Session,
    ticket: Ticket,
    workspace: Workspace,
    result: LandResult,
    config: GitAutomationConfig,
) -> Handover:
    """Put the conflict in the ticket's worktree and dispatch the resolver."""
    repo_root = resolve_workspace_root(workspace)
    worktree = WorktreeService(session, repo_path=str(repo_root)).active_worktree_for_ticket(
        ticket.id
    )
    if worktree is None or not Path(worktree.worktree_path).is_dir():
        logger.warning(
            "Landing %s conflicted but ticket %s has no worktree to resolve in",
            result.branch,
            ticket.external_id,
        )
        return Handover.REFUSED
    last_run = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id)
        .order_by(col(AgentRun.created_at).desc())
    ).first()
    if last_run is None:
        return Handover.REFUSED

    # Bring the conflict into the checkout: the merge is expected to stop with
    # markers, which is what the resolver reads. A merge that *succeeds* here
    # means the branch moved between the landing attempt and now — then there
    # is nothing to resolve, and the caller lands again.
    merged = run_git(
        ["merge", "--no-edit", result.target],
        cwd=worktree.worktree_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if merged.returncode == 0:
        logger.warning(
            "Landing %s reported conflicts but merging %s into the worktree succeeded",
            result.branch,
            result.target,
        )
        return Handover.MERGED_CLEAN

    report = request_agent_resolution(
        session,
        last_run,
        ticket,
        worktree,
        Path(worktree.worktree_path),
        max_attempts=config.max_conflict_resolve_attempts,
    )
    if report is None or not report.resolution_attempted:
        # Budget spent or dispatch refused. Leave the tree clean for a person.
        run_git(
            # silent-ok: cleanup before blocking; the block message names the
            # conflicted files whether or not the abort succeeded
            ["merge", "--abort"],
            cwd=worktree.worktree_path,
            check=False,
            capture_output=True,
        )
        return Handover.REFUSED
    return Handover.DISPATCHED
