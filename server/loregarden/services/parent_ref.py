"""Name a worktree parent as this checkout can see it, and its live merge-base.

Git only: no ORM, no artifacts. The composer asks these two questions and
decides whether to capture a diff; this module does not capture.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.services.git_merge_noco import merge_base
from loregarden.services.git_subprocess import run_git
from loregarden.services.publish_ref import comparison_ref

logger = logging.getLogger(__name__)


def parent_comparison_ref(repo_root: Path, parent_branch: str) -> str | None:
    """The ref the ticket branch was cut from, as this checkout can name it.

    Returns the ref name, never a SHA. Silent when nothing resolves — retirement
    already has its keep-reason path, and the composer treats None as fail-closed.
    """
    for ref in dict.fromkeys((comparison_ref(parent_branch), parent_branch)):
        probe = run_git(
            ["rev-parse", "--verify", "--quiet", ref],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return ref
    return None


def live_diff_base(repo_root: Path, parent_branch: str) -> str | None:
    """The live merge-base of HEAD and the named parent, as a SHA, or None.

    Fail-closed: a parent this checkout cannot name, unrelated histories, or
    git failing to compute the merge-base are all None plus a warning. Never a
    branch name. Never a frozen Worktree column.
    """
    named = parent_comparison_ref(repo_root, parent_branch)
    if named is None:
        logger.warning(
            "Cannot name parent branch %s in %s",
            parent_branch,
            repo_root,
        )
        return None
    try:
        sha = merge_base(repo_root, "HEAD", named)
    except RuntimeError:
        logger.warning(
            "merge-base HEAD %s failed in %s",
            named,
            repo_root,
        )
        return None
    if not sha:
        logger.warning(
            "merge-base HEAD %s is empty in %s",
            named,
            repo_root,
        )
        return None
    return sha
