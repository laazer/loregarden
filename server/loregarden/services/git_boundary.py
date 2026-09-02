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
import subprocess
from pathlib import Path

from loregarden.models.domain import AgentRun, GitBoundary
from loregarden.services.git_commit_push_service import head_commit_sha, working_tree_paths
from loregarden.services.git_subprocess import run_git
from sqlmodel import Session


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
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def read_boundary(repo_root: Path, *, dirty_paths: set[str] | None = None) -> GitBoundary:
    """The boundary of `repo_root`, or an empty boundary if it cannot be read.

    `dirty_paths` is accepted because the dispatch path already computes it to
    bracket the run's own edits; passing it in keeps this to one extra git call
    per dispatch instead of two.
    """
    try:
        if not repo_root.is_dir():
            return GitBoundary()
        # `or set()` keeps a boundary check that cannot read the tree from
        # claiming a clean one — the caller's own verdict logic treats an
        # empty set as 'nothing to attribute', which is the safe reading here.
        paths = (working_tree_paths(repo_root) or set()) if dirty_paths is None else dirty_paths
        return GitBoundary(
            repo_path=str(repo_root),
            branch=current_branch(repo_root),
            head_sha=head_commit_sha(repo_root),
            dirty_paths=sorted(paths),
        )
    except (OSError, subprocess.SubprocessError):
        return GitBoundary()


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
