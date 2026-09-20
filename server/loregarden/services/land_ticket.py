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
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_subprocess import run_git
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


def _git(repo_root: Path, *args: str):
    return run_git(list(args), cwd=str(repo_root), check=False, capture_output=True, text=True)


def _rev(repo_root: Path, ref: str) -> str:
    result = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    return _git(repo_root, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def _merge_tree(repo_root: Path, target: str, branch: str) -> tuple[str, tuple[str, ...], str]:
    """The merged tree, the conflicted paths (empty when clean), and git's own words.

    ``merge-tree --write-tree`` exits 0 with the tree on the first line, 1 with
    the tree and then the conflicted paths up to a blank line, and higher when
    it could not merge at all — that last case is reported as a failure with
    no tree, never as "no conflicts".
    """
    result = _git(repo_root, "merge-tree", "--write-tree", "--name-only", target, branch)
    lines = result.stdout.splitlines()
    if result.returncode == 0:
        return lines[0].strip() if lines else "", (), ""
    if result.returncode == 1 and lines:
        files: list[str] = []
        for line in lines[1:]:
            if not line.strip():
                break
            files.append(line.strip())
        return lines[0].strip(), tuple(files), result.stdout.strip()
    return "", (), (result.stderr or result.stdout or "git merge-tree failed").strip()


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

    branch_sha = _rev(repo_root, f"refs/heads/{branch}")
    if not branch_sha:
        return LandResult(ok=True, branch=branch, target=target, skipped=LandSkip.NO_BRANCH)
    if target == base:
        return LandResult(ok=True, branch=branch, target=target, skipped=LandSkip.BASE_TARGET)

    ensure_integration_branch(repo_root, target, base)
    target_sha = _rev(repo_root, f"refs/heads/{target}")
    if _is_ancestor(repo_root, branch_sha, target_sha):
        _record(session, ticket, branch_sha, target)
        return LandResult(
            ok=True,
            branch=branch,
            target=target,
            landed_sha=branch_sha,
            skipped=LandSkip.ALREADY_LANDED,
        )

    tree, conflicts, words = _merge_tree(repo_root, target, branch)
    if conflicts:
        return LandResult(
            ok=False,
            branch=branch,
            target=target,
            detail=words,
            conflicted_files=conflicts,
        )
    if not tree:
        return LandResult(ok=False, branch=branch, target=target, detail=words)

    subject = f"Land {ticket.external_id or ticket.id[:8]}: {ticket.title}".strip()
    committed = _git(
        repo_root, "commit-tree", tree, "-p", target_sha, "-p", branch_sha, "-m", subject
    )
    if committed.returncode != 0:
        detail = (committed.stderr or committed.stdout or "git commit-tree failed").strip()
        return LandResult(ok=False, branch=branch, target=target, detail=detail)
    merge_sha = committed.stdout.strip()

    moved = _git(repo_root, "update-ref", f"refs/heads/{target}", merge_sha, target_sha)
    if moved.returncode != 0:
        # The old value did not match: something else landed on this target
        # since we read it. Reported, not retried — the caller re-lands on its
        # next completion with a fresh read, and nothing was written.
        detail = (moved.stderr or moved.stdout or "git update-ref refused").strip()
        return LandResult(ok=False, branch=branch, target=target, detail=detail)

    _record(session, ticket, merge_sha, target)
    logger.info("Landed %s (%s) on %s as %s", ticket.external_id, branch, target, merge_sha[:12])
    return LandResult(ok=True, branch=branch, target=target, landed_sha=merge_sha)


def _record(session: Session, ticket: Ticket, sha: str, target: str) -> None:
    ticket.landed_sha = sha
    ticket.landed_branch = target
    session.add(ticket)
    session.commit()
