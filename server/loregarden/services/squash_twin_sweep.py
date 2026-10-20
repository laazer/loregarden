"""Find ticket branches holding a squash of their target, and give them its history.

`branch_takes_target` repairs such a branch when its next stage checks it out.
This finds the ones no stage is about to check out, so the invariant
`ticket_branches_hold_their_targets_history` (lg-external-harness-1318) can
repair them on the reconcile timer and report one that entered the state after
publishing stopped pushing a squash under the branch's own name (#654).

Only open tickets whose target is an integration branch are asked, and only
when nothing is running on them: a live stage's own checkout repairs it, and a
ref moved under a running agent would race its commits.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import AgentRun, OrchestrationRun, Ticket, TicketState, Workspace
from loregarden.services.branch_takes_target import record_twin
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_merge_noco import SquashTwin, read_branch_tips, squash_twin
from loregarden.services.parallel_queue import LIVE_ORCHESTRATION_STATUSES, LIVE_RUN_STATUSES
from loregarden.services.publish_ref import INTEGRATION_PREFIX
from loregarden.services.target_branch import TargetBranchError, target_branch_name
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

_CLOSED = (TicketState.DONE, TicketState.WONT_DO)


@dataclass(frozen=True)
class TwinnedBranch:
    ticket: Ticket
    repo_root: Path
    branch: str
    branch_sha: str
    target: str
    twin: SquashTwin


def twinned_branches(session: Session, *, ticket_id: str | None = None) -> list[TwinnedBranch]:
    """Open, idle tickets whose branch holds a squash of its integration branch. Read-only.

    A repository git cannot read is logged and skipped, never answered as clean
    for the rest: one broken checkout must not hide the others.
    """
    live_runs = select(AgentRun.ticket_id).where(col(AgentRun.status).in_(LIVE_RUN_STATUSES))
    live_orchestrations = select(OrchestrationRun.ticket_id).where(
        col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES)
    )
    query = select(Ticket).where(
        col(Ticket.workspace_id).is_not(None),
        col(Ticket.state).not_in(_CLOSED),
        col(Ticket.id).not_in(live_runs),
        col(Ticket.id).not_in(live_orchestrations),
    )
    if ticket_id:
        query = query.where(Ticket.id == ticket_id)
    by_workspace: dict[str, list[Ticket]] = defaultdict(list)
    for ticket in session.exec(query).all():
        by_workspace[ticket.workspace_id or ""].append(ticket)
    found: list[TwinnedBranch] = []
    for workspace_id, tickets in by_workspace.items():
        workspace = session.get(Workspace, workspace_id)
        if workspace is not None and workspace.archived_at is None:
            found.extend(_twinned_in(session, workspace, tickets))
    return found


def _twinned_in(
    session: Session, workspace: Workspace, tickets: list[Ticket]
) -> list[TwinnedBranch]:
    """One workspace's tickets, with every ref read in one `for-each-ref`."""
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        # silent-ok: no checkout holds no branch to repair; integration_branch_health
        # skips it the same way, and the workspace page shows its repository missing
        return []
    pairs = [
        (ticket, resolve_ticket_branch(ticket), target)
        for ticket in tickets
        if (target := _integration_target(session, ticket, workspace))
    ]
    if not pairs:
        return []
    tips = read_branch_tips(
        repo_root, *{name for _, branch, target in pairs for name in (branch, target)}
    )
    if tips.error:
        logger.warning("Could not read branches in %s: %s", repo_root, tips.error)
        return []
    found: list[TwinnedBranch] = []
    for ticket, branch, target in pairs:
        branch_sha, target_sha = tips.of(branch), tips.of(target)
        if not branch_sha or not target_sha:
            continue
        try:
            twin = squash_twin(repo_root, branch_sha, target_sha)
        except RuntimeError:
            logger.warning(
                "Could not tell whether %s holds a squash of %s in %s",
                branch,
                target,
                repo_root,
                exc_info=True,
            )
            continue
        if twin is not None:
            found.append(TwinnedBranch(ticket, repo_root, branch, branch_sha, target, twin))
    return found


def _integration_target(session: Session, ticket: Ticket, workspace: Workspace) -> str:
    """The integration branch ``ticket`` lands on, or "" when it lands on the base."""
    try:
        target = target_branch_name(session, ticket, workspace)
    except TargetBranchError:
        return ""  # silent-ok: no tree to land in, so no integration branch to hold a squash of
    return target if target.startswith(INTEGRATION_PREFIX) else ""


def record_twins(session: Session, twinned: list[TwinnedBranch]) -> int:
    """Record each twin as merged into its branch; how many were. Failures are logged."""
    recorded = 0
    for item in twinned:
        taken = record_twin(item.repo_root, item.branch, item.branch_sha, item.twin)
        if taken.ok:
            recorded += 1
        else:
            logger.warning("%s: %s", item.ticket.external_id, taken.detail)
    return recorded
