"""Read the git boundary a run executes against, and record it on the run.

A run's boundary is the state of the tree it started from. Nothing recorded it
until now: `agent_runs.changed_paths_json` says what a run left behind, but not
what it inherited, so no later stage could tell whether the tree had moved
underneath it — a branch switched by a concurrent session, a squash-merge landed
mid-ticket, a worktree that was expected and did not exist all looked identical
to a clean handoff.

The read is deliberately total. Every helper here answers with an empty value
rather than raising, and the boundary that results is *unknown*, which callers
must not confuse with *changed*. A stage refusing to run because git was
unreadable would be a worse failure than the one this exists to catch.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from loregarden.models.domain import AgentRun, GitBoundary
from loregarden.services.git_commit_push_service import head_commit_sha
from loregarden.services.git_subprocess import run_git
from loregarden.services.worktree_snapshot import TreeSnapshot, read_tree
from sqlmodel import Session

logger = logging.getLogger(__name__)


def current_branch(repo_root: Path) -> str:
    """The checked-out branch, or "" when detached, empty, or not a repo.

    `symbolic-ref` rather than `rev-parse --abbrev-ref HEAD`, which answers the
    literal string "HEAD" on a detached head and would record that as a branch
    name.
    """
    try:
        proc = run_git(
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    except OSError:
        logger.warning(
            "Could not read the checked-out branch of %s; recording an unknown boundary",
            repo_root,
            exc_info=True,
        )
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def read_boundary(repo_root: Path) -> GitBoundary:
    """The boundary of `repo_root`, or an empty boundary if it cannot be read."""
    try:
        if not repo_root.is_dir():
            return GitBoundary()
        return boundary_of_tree(read_tree(repo_root))
    except (OSError, subprocess.SubprocessError):
        logger.warning(
            "Could not read the git boundary of %s; the run starts from an unknown tree",
            repo_root,
            exc_info=True,
        )
        return GitBoundary()


def boundary_of_tree(tree: TreeSnapshot) -> GitBoundary:
    """The boundary a snapshot already describes, with no further git call.

    The dispatch path takes one snapshot before the agent runs and brackets the
    run's edits with it; the boundary is the same moment, so it is read from the
    same answer rather than asked for again (lg-build-verification-847).

    A snapshot git could not take falls back to asking for HEAD and the branch
    on their own, as this did before there was a snapshot: a `status` that fails
    does not mean `rev-parse` will, and a recorded sha is worth the two calls on
    a path that is already failing. The dirty paths then read as none — the
    caller's own verdict logic treats an empty set as "nothing to attribute",
    which is the safe reading here.
    """
    repo_root = tree.repo_root
    if tree.dirty_paths is None:
        return GitBoundary(
            repo_path=str(repo_root),
            branch=current_branch(repo_root),
            head_sha=head_commit_sha(repo_root),
            dirty_paths=[],
        )
    return GitBoundary(
        repo_path=str(repo_root),
        branch=tree.branch,
        head_sha=tree.head_sha,
        dirty_paths=sorted(tree.dirty_paths),
    )


def boundary_of_run(run: AgentRun) -> GitBoundary:
    """The boundary stored on a run. Rows written before the columns existed,
    and runs whose repo could not be read, both read back as unrecorded."""
    return GitBoundary(
        repo_path=run.start_repo_path,
        branch=run.start_branch,
        head_sha=run.start_head_sha,
        dirty_paths=json.loads(run.start_dirty_paths_json or "[]"),
    )


def stamp_run_boundary(session: Session, run: AgentRun, boundary: GitBoundary) -> None:
    """Persist the boundary a run started from."""
    run.start_repo_path = boundary.repo_path
    run.start_branch = boundary.branch
    run.start_head_sha = boundary.head_sha
    run.start_dirty_paths_json = json.dumps(boundary.dirty_paths)
    session.add(run)
    session.commit()
