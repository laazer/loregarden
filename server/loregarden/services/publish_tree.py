"""Publish a finished tree's integration branch to the base (lg-milestone-that-771).

The last leg. Tickets land on their tree's integration branch (768); when the
tree's root completes, the branch goes to the base through the workspace's
publish chain — push, and then a PR and auto-merge only if the workspace says
so. The default here is push and stop: this repository has no branch
protection, so `gh pr merge --auto` merges immediately, and opening a PR stays
a person's decision. One PR per tree rather than one per ticket.

Runs once per tip: a root that completes again (a requeue, a re-run) with
nothing new on the branch publishes nothing. A failure is recorded on the root
ticket and emitted, never only logged — the tree looked finished, and this is
the step that decides whether it is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import EventType, Ticket, Workspace
from loregarden.services.git_automation import AutomationResult, PublishSubject, publish_branch
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_merge_noco import rev
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
    #: Nothing to do: not a root, no integration branch, or this tip is
    #: already published.
    skipped: bool = False


def publish_tree(session: Session, ticket: Ticket, workspace: Workspace) -> PublishOutcome:
    """Push the tree's integration branch if ``ticket`` is its root and the tip is new."""
    repo_root = resolve_workspace_root(workspace)
    if subtree_root(session, ticket).id != ticket.id:
        return PublishOutcome(ok=True, branch="", skipped=True)
    branch = integration_branch_for(ticket)
    tip = rev(repo_root, f"refs/heads/{branch}")
    if not tip:
        return PublishOutcome(ok=True, branch=branch, skipped=True)
    if ticket.landed_branch == branch and ticket.landed_sha == tip:
        return PublishOutcome(ok=True, branch=branch, tip=tip, skipped=True)

    config = resolve_git_automation(workspace, ticket)
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
                f"- Tree: `{ticket.external_id}`",
                f"- Integration branch: `{branch}`",
                "",
                "_Opened automatically by the Loregarden orchestrator when the tree completed._",
            ]
        ),
    )


def _detail(result: AutomationResult) -> str:
    failure = result.failure
    if failure is not None:
        return f"{failure.step} failed: {failure.detail}"
    return "; ".join(f"{step.step}: {step.detail}" for step in result.steps)
