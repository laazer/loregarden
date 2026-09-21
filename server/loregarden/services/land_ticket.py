"""Land a finished ticket: merge its branch into its target (lg-milestone-that-768).

The one step between "every stage passed" and "the next ticket can build on
this". It ran nowhere: `git_automation`'s publish chain had no caller on the
ticket path, so a ticket reached `done` with its branch local and unmerged,
and a dependent cut from the target found none of it.

The merge is made without a checkout — ``merge-tree`` for the result,
``commit-tree`` for the commit, ``update-ref`` to move the target — so it
works while the primary checkout and any number of worktrees hold other
branches, and the ref move is compare-and-swap so two landings racing on one
integration branch cannot overwrite each other.

Only integration branches are landed here. A ticket whose target is the base
branch is not merged locally: the base is what the primary checkout usually
has checked out, and moving it under a working tree leaves that tree showing
the landing in reverse. That leg is the publish chain's (771).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_merge_noco import merge_without_checkout, rev
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.target_branch import (
    ensure_integration_branch,
    target_branch_name,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)


class LandSkip(str, Enum):
    """Why an ok landing merged nothing."""

    #: The ticket never had a branch in the repository — nothing ran on it.
    NO_BRANCH = "no_branch"
    #: Every commit on the branch is already reachable from the target.
    ALREADY_LANDED = "already_landed"
    #: The target is the base branch; landing there is the publish chain's leg.
    BASE_TARGET = "base_target"
    #: The workspace has no git repository at its root — nothing ran in one.
    NO_REPOSITORY = "no_repository"


@dataclass(frozen=True)
class LandResult:
    ok: bool
    branch: str
    target: str
    landed_sha: str = ""
    detail: str = ""
    skipped: LandSkip | None = None
    conflicted_files: tuple[str, ...] = field(default_factory=tuple)

    @property
    def conflicted(self) -> bool:
        return bool(self.conflicted_files)


def land_ticket(session: Session, ticket: Ticket, workspace: Workspace) -> LandResult:
    """Merge the ticket's branch into its target and record where it landed.

    Idempotent: a branch already contained in the target records its tip and
    merges nothing. A conflict is returned, never raised — the caller decides
    between the resolver and a block — and every other failure carries git's
    text so the ticket can be blocked with it.
    """
    repo_root = resolve_workspace_root(workspace)
    branch = resolve_ticket_branch(ticket)
    base = resolve_orchestration_profile(workspace).git.base_branch
    target = target_branch_name(session, ticket, workspace)

    if not (repo_root / ".git").exists():
        # A run never starts in a workspace whose root is not a repository
        # (the executor refuses the dispatch), so a workflow finishing here
        # has no work in git to land. Skipped, and the event says so.
        return LandResult(ok=True, branch=branch, target=target, skipped=LandSkip.NO_REPOSITORY)
    if not rev(repo_root, f"refs/heads/{branch}"):
        return LandResult(ok=True, branch=branch, target=target, skipped=LandSkip.NO_BRANCH)
    if target == base:
        return LandResult(ok=True, branch=branch, target=target, skipped=LandSkip.BASE_TARGET)

    ensure_integration_branch(repo_root, target, base)
    subject = f"Land {ticket.external_id or ticket.id[:8]}: {ticket.title}".strip()
    outcome = merge_without_checkout(repo_root, target=target, source=branch, subject=subject)
    if not outcome.ok:
        return LandResult(
            ok=False,
            branch=branch,
            target=target,
            detail=outcome.detail,
            conflicted_files=outcome.conflicted_files,
        )

    _record(session, ticket, outcome.sha, target)
    if outcome.already_contained:
        return LandResult(
            ok=True,
            branch=branch,
            target=target,
            landed_sha=outcome.sha,
            skipped=LandSkip.ALREADY_LANDED,
        )
    logger.info("Landed %s (%s) on %s as %s", ticket.external_id, branch, target, outcome.sha[:12])
    return LandResult(ok=True, branch=branch, target=target, landed_sha=outcome.sha)


def _record(session: Session, ticket: Ticket, sha: str, target: str) -> None:
    ticket.landed_sha = sha
    ticket.landed_branch = target
    session.add(ticket)
    session.commit()
