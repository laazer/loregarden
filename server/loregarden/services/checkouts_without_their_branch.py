"""`worktrees` rows whose checkout is on disk while the branch it records is gone.

The re-entry half of lg-external-harness-1166. Discarding a losing fan-out
attempt dropped `cleanup_worktree`'s answer and force-deleted the attempt's
branch anyway, so a removal that failed left the checkout on disk holding
commits whose only ref had just been deleted. The discard now keeps the branch
whenever the removal fails; a checkout whose branch is missing is that guard,
or another deleter, failing.

Report-only: the commits may still be recoverable from the checkout, and only a
person can say whether they are wanted.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from loregarden.models.domain import Workspace, Worktree
from loregarden.services.directory_release import DirectoryState, directory_state
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def _local_branches(repo_root: Path) -> set[str] | None:
    """Every local branch name in `repo_root`, or None when git will not say."""
    listed = run_git(
        ["for-each-ref", "--format=%(refname:short)", "refs/heads"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        logger.warning(
            "Could not list branches in %s (%s); its checkouts are not judged this pass",
            repo_root,
            (listed.stderr or "").strip(),
        )
        return None
    return set(listed.stdout.split())


def _present(row: Worktree) -> bool:
    try:
        return directory_state(row.worktree_path) is DirectoryState.PRESENT
    except OSError:
        logger.warning(
            "Worktree %s at %s: the filesystem would not say whether it is there",
            row.id,
            row.worktree_path,
            exc_info=True,
        )
        return False


def checkouts_without_their_branch(session: Session) -> list[Worktree]:
    """Every row with a checkout on disk whose recorded branch no longer exists.

    Any state: a CLEANUP row with a surviving checkout is
    `cleaned_worktree_rows_leave_no_checkout`'s too, and both are true of it.
    Branches are listed once per repository, not once per row.
    """
    rows = session.exec(
        select(Worktree)
        .where(col(Worktree.worktree_path) != "", col(Worktree.branch) != "")
        .order_by(Worktree.id)
    ).all()
    by_workspace: dict[str, list[Worktree]] = defaultdict(list)
    for row in rows:
        by_workspace[row.workspace_id].append(row)
    missing: list[Worktree] = []
    for workspace_id, members in by_workspace.items():
        present = [row for row in members if _present(row)]
        workspace = session.get(Workspace, workspace_id) if workspace_id else None
        if not present or workspace is None:
            continue
        branches = _local_branches(resolve_workspace_root(workspace))
        if branches is None:
            continue
        missing.extend(row for row in present if row.branch not in branches)
    return sorted(missing, key=lambda row: row.id)
