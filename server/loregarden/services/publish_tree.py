"""Publish a finished root's work to the base (lg-milestone-that-771).

The last leg. Tickets in a tree land on its integration branch (768), and the
branch goes to the base through the workspace's publish chain — push, and then
a PR and auto-merge only if the workspace says so (`tree_open_pr`,
`tree_auto_merge`). One PR per tree rather than one per ticket.

It publishes after every landing on the branch (`tree_publish_scheduler`, 958),
and again when the root completes. Publishing only at completion kept a branch
alive as long as its milestone, weeks against a base taking ~96 commits a week,
and every integration conflict filed was on such a branch (491: 29 commits
unpublished since 09-21).

What is pushed is not the branch's history but one commit cut from the base
tip, carrying the branch merged into it, signed where the repository signs
(`git_merge_noco.squash_onto`). The PR is the tree's net change, and the
branch's own commits never reach the base. That history can carry unsigned
commits: a ticket branch cut before 782 brought seven back into 491's branch
on 2026-10-14, a PR with them can never merge under a signed-commits rule
(PR #400, superseded by #402 for exactly that), and refusing the push, as this
module did until then, held the tree's every later landing back with them.

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

Commits a CI repair turn made on the root's own branch are folded into the
integration branch first, so they are what gets pushed (`root_integration_sync`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import EventType, Ticket, Workspace
from loregarden.services.git_automation import (
    AutomationResult,
    PublishSubject,
    generated_body,
    publish_branch,
)
from loregarden.services.git_automation_config import enabled_steps, resolve_git_automation
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_merge_noco import MergeOutcome, adds_nothing, rev, squash_onto
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.root_integration_sync import fold_root_branch
from loregarden.services.target_branch import integration_branch_for, subtree_root
from loregarden.services.tree_ux_section import (
    TreeUxSection,
    close_missing_ux_answers,
    report_missing_ux_answers,
    tree_ux_section,
)
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
    #: The chain step that failed (`push`, `open_pr`, `auto_merge`), or "" when
    #: the publish failed before the chain ran. Every step before it succeeded.
    failed_step: str = ""


def publish_tree(session: Session, ticket: Ticket, workspace: Workspace) -> PublishOutcome:
    """Push the work ``ticket`` roots, if it is a root and the tip is new."""
    repo_root = resolve_workspace_root(workspace)
    if subtree_root(session, ticket).id != ticket.id or not (repo_root / ".git").exists():
        return PublishOutcome(ok=True, branch="", skipped=True)
    integration = integration_branch_for(ticket)
    folded = fold_root_branch(repo_root, ticket, integration)
    if folded is not None and not folded.ok:
        failed = PublishOutcome(
            ok=False,
            branch=integration,
            detail=f"the root's branch could not be folded into {integration}: {folded.detail}",
        )
        _emit(session, ticket, resolve_git_automation(workspace, ticket).base_branch, failed)
        return failed
    branch, tip = published_branch(repo_root, ticket)
    if not tip:
        return PublishOutcome(ok=True, branch=branch, skipped=True)
    if ticket.landed_branch == branch and ticket.landed_sha == tip:
        return PublishOutcome(ok=True, branch=branch, tip=tip, skipped=True)

    config = resolve_git_automation(workspace, ticket)
    base_sha = rev(repo_root, f"refs/heads/{config.base_branch}")
    try:
        already_there = adds_nothing(repo_root, base_sha, tip)
    except RuntimeError as exc:
        failed = PublishOutcome(ok=False, branch=branch, tip=tip, detail=str(exc))
        _emit(session, ticket, config.base_branch, failed)
        return failed
    if already_there:
        # A ticket that committed nothing, or work that reached the base by
        # another route: merged by hand, or squash-merged from this branch's
        # own PR, which ancestry cannot see. A push would publish a branch with
        # nothing on it, and its PR would be empty.
        return PublishOutcome(ok=True, branch=branch, tip=tip, skipped=True)
    chain = tree_chain(config)
    prepared = _prepare(
        session, repo_root, ticket, branch=branch, base_sha=base_sha, tip=tip, config=chain
    )
    if prepared.failure:
        failed = PublishOutcome(ok=False, branch=branch, tip=tip, detail=prepared.failure)
        _emit(session, ticket, config.base_branch, failed)
        return failed
    result = publish_branch(repo_root, prepared.subject, chain)
    if result.ok and result.pr_url:
        _settle_ux_card(session, ticket, prepared.section, result.pr_url)
    outcome = PublishOutcome(
        ok=result.ok,
        branch=branch,
        tip=tip,
        pr_url=result.pr_url,
        detail=_detail(result),
        skipped=not result.steps,
        failed_step=result.failure.step if result.failure else "",
    )
    if result.ok and result.steps:
        ticket.landed_branch = branch
        ticket.landed_sha = tip
        session.add(ticket)
        session.commit()
        logger.info("Published %s at %s for %s", branch, tip[:12], ticket.external_id)
    _emit(session, ticket, config.base_branch, outcome)
    return outcome


@dataclass(frozen=True)
class _Prepared:
    """What to publish, or why it cannot be: the PR's subject and UX section."""

    subject: PublishSubject
    section: TreeUxSection = TreeUxSection()
    failure: str = ""


def _prepare(
    session: Session,
    repo_root: Path,
    ticket: Ticket,
    *,
    branch: str,
    base_sha: str,
    tip: str,
    config: GitAutomationConfig,
) -> _Prepared:
    """Cut the commit to publish, and describe it, user-facing surfaces included."""
    subject = _subject(ticket, branch)
    if "push" not in enabled_steps(config):
        return _Prepared(subject)
    cut = squash_onto(repo_root, base_sha, tip, message=subject.commit_message)
    if not cut.ok:
        return _Prepared(subject, failure=_cut_failure(branch, config, cut))
    try:
        section = tree_ux_section(
            session, repo_root, ticket, branch=branch, base_sha=base_sha, head_sha=cut.sha
        )
    except RuntimeError as exc:
        return _Prepared(
            subject, failure=f"could not tell which user-facing files {branch} changes: {exc}"
        )
    body = generated_body(subject.pr_body + section.text)
    return _Prepared(
        replace(subject, head=cut.sha, pr_body=body, refresh_body=True), section=section
    )


def _settle_ux_card(session: Session, root: Ticket, section: TreeUxSection, pr_url: str) -> None:
    """Name the tickets the PR's UX section lacks answers from, or close the card."""
    if section.missing:
        report_missing_ux_answers(session, root, section.missing, pr_url)
    else:
        close_missing_ux_answers(session, root)


def _emit(session: Session, ticket: Ticket, base_branch: str, outcome: PublishOutcome) -> None:
    event_bus.publish(
        session,
        EventType.TICKET_LANDED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        payload={
            "ok": outcome.ok,
            "branch": outcome.branch,
            "target": base_branch,
            "landed_sha": outcome.tip if outcome.ok else "",
            "pr_url": outcome.pr_url,
            "published": True,
            "skipped": "already_published" if outcome.skipped else "",
            "detail": outcome.detail,
        },
    )


def published_branch(repo_root: Path, root: Ticket) -> tuple[str, str]:
    """The branch a root publishes, and its tip ("" when it has none).

    The integration branch when the root has a tree on one, else its own.
    """
    integration = integration_branch_for(root)
    tip = rev(repo_root, f"refs/heads/{integration}")
    if tip:
        return integration, tip
    own = resolve_ticket_branch(root)
    return own, rev(repo_root, f"refs/heads/{own}")


def tree_chain(config: GitAutomationConfig) -> GitAutomationConfig:
    """``config`` with the tree switches folded into the PR and merge steps."""
    return config.model_copy(
        update={
            "open_pr": config.open_pr or config.tree_open_pr,
            "auto_merge": config.auto_merge or config.tree_auto_merge,
        }
    )


def _cut_failure(branch: str, config: GitAutomationConfig, cut: MergeOutcome) -> str:
    """Why the commit to publish could not be cut, and what clears it."""
    base = config.base_branch
    if cut.conflicted:
        return (
            f"{branch} conflicts with {base} in {', '.join(cut.conflicted_files)}, so no "
            f"commit could be cut from {base} to publish it. It publishes once the branch "
            f"takes {base}: the integration sweep gives the conflict a merge turn."
        )
    return f"could not cut the commit that publishes {branch} onto {base}: {cut.detail}"


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
                "_Opened by the Loregarden orchestrator, and updated each time work lands on "
                "the branch._",
            ]
        ),
    )


def _detail(result: AutomationResult) -> str:
    failure = result.failure
    if failure is not None:
        return f"{failure.step} failed: {failure.detail}"
    return "; ".join(f"{step.step}: {step.detail}" for step in result.steps)
