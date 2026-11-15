"""The one owner of branch moves: guard, keep, move, record.

`integration/lg-milestone-that-491` lost lg-milestone-that-1316's landing
between 10-20 and 10-30, and which writer dropped it could not be recovered:
the branch was later deleted and recreated, and its reflog went with it. Every
writer was a bare `update-ref` or `branch -D` in its own service, each with its
own idea of what was safe to leave behind.

`move_integration_branch` is now the only place a branch the control plane owns
is moved, created or deleted. Every move:

1. runs the landing guard (`landing_survival.refuse_dropping_landings`), unless
   it is a fast forward, which by construction keeps everything the old tip had;
2. keeps the old tip under `settled_refs.SETTLED_PREFIX` before any move that is
   not a fast forward, so `base_delivery` still finds the landings it held;
3. moves the ref by compare-and-swap with ``-m <writer>: <why>``, so the reflog
   names the writer too (`enable_ref_logs` makes git keep one for every ref);
4. appends an `IntegrationBranchMove` row naming the branch, both tips, the
   writer, the reason, the run, and the UTC time. The row outlives the branch.

The `py-ref-writers` gate keeps new bare ref writes out of every other module.
Writers that publish through the generic merge helpers (`git_merge_noco`) pass
`mover(...)`, which those helpers require for an integration branch.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import IntegrationBranchMove, Workspace
from loregarden.services.active_workspaces import active_workspaces
from loregarden.services.git_merge_noco import MoveRef, is_ancestor, rev
from loregarden.services.git_subprocess import run_git
from loregarden.services.landing_survival import LandingsWouldBeLost, refuse_dropping_landings
from loregarden.services.settled_refs import SETTLED_PREFIX
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


class BranchMoveRefused(RuntimeError):
    """Git refused the move: the branch moved since it was read, or the ref is bad."""


def _git(repo_root: Path, *args: str):
    return run_git(list(args), cwd=str(repo_root), check=False, capture_output=True, text=True)


def _words(result, fallback: str) -> str:
    return (result.stderr or result.stdout or fallback).strip()


def _keep(repo_root: Path, branch: str, tip: str) -> None:
    """Keep ``tip`` under `SETTLED_PREFIX` before ``branch`` leaves it; raises when git refuses."""
    kept = _git(repo_root, "update-ref", f"{SETTLED_PREFIX}{branch}/{tip}", tip)
    if kept.returncode != 0:
        raise BranchMoveRefused(
            f"Could not keep {branch!r} at {tip[:12]} before moving it: "
            f"{_words(kept, 'git update-ref refused')}"
        )


def _update(repo_root: Path, branch: str, *, new_tip: str, old_tip: str, message: str) -> None:
    ref = f"refs/heads/{branch}"
    if new_tip:
        # An empty old value makes git refuse unless the ref does not exist yet.
        moved = _git(repo_root, "update-ref", "-m", message, ref, new_tip, old_tip)
    else:
        moved = _git(repo_root, "update-ref", "-m", message, "-d", ref, old_tip)
    if moved.returncode != 0:
        raise BranchMoveRefused(
            f"{branch} was not moved from {old_tip[:12] or 'nothing'} to "
            f"{new_tip[:12] or 'nothing'}: {_words(moved, 'git update-ref refused')}"
        )


def move_integration_branch(
    session: Session,
    workspace: Workspace,
    branch: str,
    new_tip: str,
    why: str,
    *,
    writer: str,
    expected: str | None = None,
    run_id: str | None = None,
    repo_root: Path | None = None,
) -> IntegrationBranchMove | None:
    """Move ``branch`` to ``new_tip`` (``""`` deletes it) and record who did.

    ``expected`` is the tip the caller read and decided on; the move is refused
    (`BranchMoveRefused`) when the branch has moved since. Omitted, the current
    tip is read here. ``""`` means the branch must not exist yet (a creation).
    ``writer`` names the caller as ``module.function``.

    Raises `LandingsWouldBeLost` when the move would drop a landing the base
    does not hold, before anything is touched. Returns the ledger row, or None
    when the branch is already at ``new_tip`` and nothing moved.
    """
    root = repo_root or resolve_workspace_root(workspace)
    old_tip = rev(root, f"refs/heads/{branch}") if expected is None else expected
    if old_tip == new_tip:
        return None
    forward = bool(old_tip and new_tip) and is_ancestor(root, old_tip, new_tip)
    if old_tip and not forward:
        refuse_dropping_landings(session, workspace, branch, new_tip=new_tip)
        _keep(root, branch, old_tip)
        # One writer may both merge forward and rewind (the refresh resets to a
        # base that holds its work): say which this one was.
        why = f"{why} (not a fast forward: the old tip is kept under {SETTLED_PREFIX})"
    _update(root, branch, new_tip=new_tip, old_tip=old_tip, message=f"{writer}: {why}")
    row = IntegrationBranchMove(
        workspace_id=workspace.id,
        branch=branch,
        old_tip=old_tip,
        new_tip=new_tip,
        writer=writer,
        why=why,
        run_id=run_id,
    )
    session.add(row)
    # The ref has moved; the record of it must not wait on, or roll back with,
    # whatever else the caller has in flight.
    session.commit()
    logger.info(
        "%s moved %s from %s to %s: %s",
        writer,
        branch,
        old_tip[:12] or "nothing",
        new_tip[:12] or "nothing",
        why,
    )
    return row


def mover(
    session: Session,
    workspace: Workspace,
    *,
    writer: str,
    why: str,
    run_id: str | None = None,
    repo_root: Path | None = None,
) -> MoveRef:
    """A `MoveRef` that moves through `move_integration_branch`, for the merge helpers."""

    def move(branch: str, new_tip: str, old_tip: str) -> str:
        try:
            move_integration_branch(
                session,
                workspace,
                branch,
                new_tip,
                why,
                writer=writer,
                expected=old_tip,
                run_id=run_id,
                repo_root=repo_root,
            )
        except (BranchMoveRefused, LandingsWouldBeLost) as exc:
            return str(exc)
        return ""

    return move


def branch_moves(
    session: Session, workspace: Workspace, *, branch: str = "", limit: int = 50
) -> list[IntegrationBranchMove]:
    """The ledger for ``workspace``, newest first; one branch's when ``branch`` is given."""
    query = select(IntegrationBranchMove).where(
        col(IntegrationBranchMove.workspace_id) == workspace.id
    )
    if branch:
        query = query.where(col(IntegrationBranchMove.branch) == branch)
    query = query.order_by(col(IntegrationBranchMove.moved_at).desc()).limit(limit)
    return list(session.exec(query).all())


def enable_ref_logs(repo_root: Path) -> str:
    """Make git keep a reflog for every ref in ``repo_root``. Returns "" or why it could not.

    By default git logs only `refs/heads` and `HEAD` in a non-bare repository,
    and a worktree's checkout can set it off; `always` covers the settled refs
    and every branch the control plane creates, so a move is in the reflog too.
    """
    result = _git(repo_root, "config", "--local", "core.logAllRefUpdates", "always")
    return "" if result.returncode == 0 else _words(result, "git config failed")


def enable_ref_logs_in_workspaces(session: Session) -> int:
    """`enable_ref_logs` in every active workspace's repository; returns how many it set.

    At boot, before anything moves a branch. A repository it cannot set is
    logged by name: its moves are still in the ledger, only not in git's reflog.
    """
    enabled = 0
    for workspace in active_workspaces(session):
        root = resolve_workspace_root(workspace)
        if not (root / ".git").exists():
            continue
        refused = enable_ref_logs(root)
        if refused:
            logger.warning(
                "Could not set core.logAllRefUpdates=always in %s (%s): %s; its branch moves "
                "are recorded in integration_branch_moves only",
                workspace.slug,
                root,
                refused,
            )
            continue
        enabled += 1
    return enabled
