"""Publish a finished root's work to the base (lg-milestone-that-771).

The last leg. Tickets in a tree land on its integration branch (768); when the
tree's root completes, the branch goes to the base through the workspace's
publish chain — push, and then a PR and auto-merge only if the workspace says
so. One PR per tree rather than one per ticket.

A top-level ticket with no tree is its own root. Nothing lands on an
integration branch for it, because its target is the base, so what it
publishes is its own branch. Before this, it found no integration branch,
skipped, and reached `done` with its work only on a local branch.

The default is push and stop. Opening a PR stays a person's decision, and in
this repository auto-merge is off and a ruleset on the base requires checks,
so the merge itself is a person's too.

Runs once per tip: a root that completes again (a requeue, a re-run) with
nothing new on the branch publishes nothing, and neither does a branch the
base already contains. A failure is recorded on the root ticket and emitted,
never only logged — the tree looked finished, and this is the step that
decides whether it is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import EventType, Ticket, Workspace
from loregarden.services.git_automation import AutomationResult, PublishSubject, publish_branch
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_merge_noco import is_ancestor, rev
from loregarden.services.target_branch import integration_branch_for, subtree_root
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishOutcome:
    ok: bool
    branch: str
    tip: str = ""
    pr_url: str = ""
    detail: str = ""
    #: Nothing to do: not a root, no branch to publish, a tip the base
    #: already contains, or a tip already published.
    skipped: bool = False


def publish_tree(session: Session, ticket: Ticket, workspace: Workspace) -> PublishOutcome:
    """Push the work ``ticket`` roots, if it is a root and the tip is new."""
    repo_root = resolve_workspace_root(workspace)
    if subtree_root(session, ticket).id != ticket.id or not (repo_root / ".git").exists():
        return PublishOutcome(ok=True, branch="", skipped=True)
    branch, tip = _published_branch(repo_root, ticket)
    if not tip:
        return PublishOutcome(ok=True, branch=branch, skipped=True)
    if ticket.landed_branch == branch and ticket.landed_sha == tip:
        return PublishOutcome(ok=True, branch=branch, tip=tip, skipped=True)

    config = resolve_git_automation(workspace, ticket)
    if is_ancestor(repo_root, tip, f"refs/heads/{config.base_branch}"):
        # A ticket that committed nothing, or work already merged by hand. A
        # push would publish a branch with nothing on it, and a PR would fail.
        return PublishOutcome(ok=True, branch=branch, tip=tip, skipped=True)
    result = publish_branch(repo_root, _subject(ticket, branch), config)
    outcome = PublishOutcome(
        ok=result.ok,
        branch=branch,
        tip=tip,
        pr_url=result.pr_url,
        detail=_detail(result),
        skipped=not result.steps,
    )
    if result.ok and result.steps:
        ticket.landed_branch = branch
        ticket.landed_sha = tip
        session.add(ticket)
        session.commit()
        logger.info("Published %s at %s for %s", branch, tip[:12], ticket.external_id)
    event_bus.publish(
        session,
        EventType.TICKET_LANDED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        payload={
            "ok": outcome.ok,
            "branch": branch,
            "target": config.base_branch,
            "landed_sha": tip if outcome.ok else "",
            "pr_url": outcome.pr_url,
            "published": True,
            "skipped": "already_published" if outcome.skipped else "",
            "detail": outcome.detail,
        },
    )
    return outcome


def _published_branch(repo_root: Path, root: Ticket) -> tuple[str, str]:
    """The branch a root publishes, and its tip ("" when it has none).

    The integration branch when the root has a tree on one, else its own.
    """
    integration = integration_branch_for(root)
    tip = rev(repo_root, f"refs/heads/{integration}")
    if tip:
        return integration, tip
    own = resolve_ticket_branch(root)
    return own, rev(repo_root, f"refs/heads/{own}")


def _subject(ticket: Ticket, branch: str) -> PublishSubject:
    title = f"{ticket.external_id}: {ticket.title}"
    return PublishSubject(
        branch=branch,
        commit_message=title,
        pr_title=title,
        pr_body="\n".join(
            [
                ticket.description.strip() or "_No description._",
                "",
                f"- Ticket: `{ticket.external_id}`",
                f"- Branch: `{branch}`",
                "",
                "_Opened automatically by the Loregarden orchestrator when the ticket completed._",
            ]
        ),
    )


def _detail(result: AutomationResult) -> str:
    failure = result.failure
    if failure is not None:
        return f"{failure.step} failed: {failure.detail}"
    return "; ".join(f"{step.step}: {step.detail}" for step in result.steps)
