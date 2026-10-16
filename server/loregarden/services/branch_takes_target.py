"""Bring a ticket's branch up to its target when a plain merge conflicts.

Before a stage runs, the ticket's branch takes its target (the tree's
integration branch, or the base): a prerequisite that landed since the branch
was cut must be in the tree the agent works on (769, 779). A conflict there
raised and blocked the ticket, and nothing resolved it. On 2026-10-14 that
held three tickets and paused an initiative's autopilot: 918 and 920, whose
only commits were 801's from before 491's tree reached main (so their work was
already on the target, and only history conflicted), and 954, with seven
commits of real work against a `chat_branch_sweep.py` main had rewritten.

Two answers, tried in order, before a person is asked:

1. **The branch adds nothing the target lacks** (`superseding_commit`: some
   commit in the target's history already holds all of it, which a squash
   merge leaves invisible to ancestry). The branch is moved to the target by
   compare-and-swap. Its commits stay in the reflog, and their content is on
   the target. 918 and 920.
2. **It has work of its own**: the agent's merge turn
   (`integration_merge_agent.merge_base_by_agent`) merges the target in, in a
   throwaway worktree. Loregarden writes the commit, the workspace gates grade
   what the merge adds, and the branch moves only when it passes. Under the
   same switch and per-tip budget as an integration branch's turn. 954.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_merge_noco import merge_preview, rev, superseding_commit
from loregarden.services.git_subprocess import run_git
from loregarden.services.integration_merge_agent import merge_base_by_agent
from sqlmodel import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MergeTurnSeat:
    """What an agent's merge turn needs that a git-only caller does not have."""

    session: Session
    workspace: Workspace
    ticket: Ticket


@dataclass(frozen=True)
class TakeTarget:
    """Whether ``branch`` can now take its target; ``moved_from`` is set when
    this moved the branch, so a tree holding it can be brought along."""

    ok: bool
    detail: str = ""
    moved_from: str = ""
    moved_to: str = ""


def take_target(
    repo_root: Path, branch: str, target: str, *, seat: MergeTurnSeat | None = None
) -> TakeTarget:
    """Make ``branch`` able to take ``target``; ``ok`` means the caller's merge can go ahead."""
    branch_sha = rev(repo_root, f"refs/heads/{branch}")
    target_sha = rev(repo_root, target)
    if not branch_sha or not target_sha:
        # Not a conflict, so not this module's to resolve: a repository whose base
        # branch does not exist yet, or a branch not cut yet. Stepping aside keeps
        # the caller's own handling, which already copes with a missing start point
        # (cut from HEAD, or fall back to the shared checkout) and reports the rest.
        # Refusing here blocked a parallel stage that had always run (CI, 2026-10-15).
        return TakeTarget(True, f"nothing to take: cannot read {branch!r} and {target!r}")
    preview = merge_preview(repo_root, branch_sha, target_sha)
    if preview.ok:
        return TakeTarget(True)
    if not preview.conflicted_files:
        return TakeTarget(False, preview.detail)
    try:
        superseded = superseding_commit(repo_root, branch_sha, target_sha)
    except RuntimeError as exc:
        return TakeTarget(False, f"could not tell whether {target!r} holds {branch!r}: {exc}")
    if superseded:
        return _move(repo_root, branch, branch_sha, target_sha, holds=superseded)
    files = ", ".join(preview.conflicted_files)
    if seat is None:
        return TakeTarget(False, f"conflicts in {files}, and no merge turn can run on this path")
    resolution = merge_base_by_agent(
        seat.session,
        seat.workspace,
        seat.ticket,
        repo_root=repo_root,
        branch=branch,
        base=target,
        files=preview.conflicted_files,
        about=ticket_branch_brief(seat.ticket, branch=branch, target=target),
    )
    if not resolution.ok:
        return TakeTarget(False, f"conflicts in {files}; {resolution.detail}")
    return TakeTarget(True, moved_from=branch_sha, moved_to=rev(repo_root, f"refs/heads/{branch}"))


def ticket_branch_brief(ticket: Ticket, *, branch: str, target: str) -> str:
    """What the merge turn is told ``branch`` is, in place of an integration branch's brief."""
    return (
        f"`{branch}` is the branch of {ticket.external_id} ({ticket.title}): its stages run "
        f"on it, and the next one cannot start while it cannot take `{target}`, the branch "
        f"its work lands on. `{target}` holds work that has already landed; `{branch}` holds "
        "this ticket's own work so far."
    )


def _move(repo_root: Path, branch: str, old: str, new: str, *, holds: str) -> TakeTarget:
    moved = run_git(
        [
            "update-ref",
            "-m",
            f"loregarden: {branch} superseded; {holds[:12]} holds its work",
            f"refs/heads/{branch}",
            new,
            old,
        ],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if moved.returncode != 0:
        return TakeTarget(
            False,
            f"{branch} moved while it was being brought up: "
            f"{(moved.stderr or moved.stdout).strip()}",
        )
    logger.warning(
        "Moved %s from %s to its target's %s: %s already holds everything it adds "
        "(the old tip stays in the reflog)",
        branch,
        old[:12],
        new[:12],
        holds[:12],
    )
    return TakeTarget(True, moved_from=old, moved_to=new)


def bring_tree_along(tree: Path, moved: TakeTarget) -> None:
    """Update a clean tree whose branch ``moved`` just moved, from the old commit to the new.

    `update-ref` moves a checked-out branch without touching its files, which
    leaves the tree reading as the reverse of the move. A two-tree `read-tree`
    is the checkout git would have done; it refuses rather than overwrite.
    """
    if not moved.moved_from:
        return
    read = run_git(
        ["read-tree", "-m", "-u", moved.moved_from, moved.moved_to],
        cwd=str(tree),
        check=False,
        capture_output=True,
        text=True,
    )
    if read.returncode != 0:
        raise ValueError(
            f"{tree} could not be brought from {moved.moved_from[:12]} to "
            f"{moved.moved_to[:12]}: {(read.stderr or read.stdout).strip()}"
        )
