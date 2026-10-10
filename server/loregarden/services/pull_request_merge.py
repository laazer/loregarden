"""Merge a ticket's pull request once GitHub says it can ship, then clean up after it.

The PR tab's "Merge and clean up". Merging by hand here has two traps
(recorded the hard way): `gh pr merge --delete-branch` merges and then fails on
the local half, because another worktree holds `main`, and reads as if nothing
merged; and squash merges leave the branch looking unmerged to git, so it
lingers. This merges with a squash pinned to the head commit the operator was
shown, confirms the merge with GitHub, then removes the branch's worktrees,
the local branch and the remote branch itself, one recorded step at a time.

A cleanup step that cannot finish is reported as a failed step and the rest
still run; the merge itself is never reported as done unless GitHub says so.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from loregarden.models.domain import (
    PullRequestLookup,
    Ticket,
    Workspace,
    Worktree,
    WorktreeState,
    utcnow,
)
from loregarden.services.branch_triage_service import (
    delete_branch,
    remove_branch_worktree,
    worktree_paths_for_branch,
)
from loregarden.services.git_subprocess import run_gh, run_git
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.ticket_pull_request import PullRequestStatus, ticket_pull_request
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

GH_MERGE_TIMEOUT_SECONDS = 120


class MergeRefused(ValueError):
    """The PR is not in a state to merge: nothing was attempted."""


class MergeFailed(RuntimeError):
    """GitHub was asked to merge and did not, or would not say that it had."""


class MergeStep(BaseModel):
    step: str
    ok: bool
    detail: str


class MergeAndCleanUp(BaseModel):
    number: int
    merge_commit: str
    steps: list[MergeStep]


def merge_and_clean_up(
    session: Session, ticket: Ticket, workspace: Workspace, *, number: int, head_sha: str
) -> MergeAndCleanUp:
    """Squash-merge PR ``number`` at ``head_sha``, then remove its branch everywhere."""
    pr = _mergeable(session, ticket, workspace, number=number, head_sha=head_sha)
    repo_root = resolve_workspace_root(workspace)
    merge_commit = _merge(repo_root, pr)
    logger.info("Merged PR #%s for %s as %s", pr.number, ticket.external_id, merge_commit[:12])

    base = resolve_orchestration_profile(workspace).git.base_branch
    steps: list[MergeStep] = []
    if pr.head in (base, pr.base):
        steps.append(MergeStep(step="branch", ok=False, detail=f"{pr.head} is a base; kept"))
        return MergeAndCleanUp(number=pr.number, merge_commit=merge_commit, steps=steps)
    steps.extend(_remove_worktrees(session, workspace, repo_root, pr.head))
    steps.append(_delete_local_branch(workspace, pr.head))
    steps.append(_delete_remote_branch(repo_root, pr.head))
    for step in steps:
        if not step.ok:
            logger.warning("Cleanup after PR #%s: %s: %s", pr.number, step.step, step.detail)
    return MergeAndCleanUp(number=pr.number, merge_commit=merge_commit, steps=steps)


def _mergeable(
    session: Session, ticket: Ticket, workspace: Workspace, *, number: int, head_sha: str
) -> PullRequestStatus:
    current = ticket_pull_request(session, ticket, workspace)
    pr = current.pull_request
    if current.lookup is PullRequestLookup.FAILED:
        raise MergeRefused(f"Could not ask GitHub about the PR first: {current.error}")
    if pr is None or pr.number != number:
        raise MergeRefused(f"#{number} is no longer this ticket's pull request; refresh the tab")
    if pr.head_sha != head_sha:
        raise MergeRefused(
            f"#{number} has new commits since you looked; refresh and check them before merging"
        )
    if not pr.mergeable_now:
        raise MergeRefused(
            f"GitHub says #{number} cannot merge yet (checks, review, signatures or conflicts)"
        )
    return pr


def _merge(repo_root: Path, pr: PullRequestStatus) -> str:
    args = ["pr", "merge", str(pr.number), "--squash", "--match-head-commit", pr.head_sha]
    try:
        proc = run_gh(args, cwd=repo_root, timeout=GH_MERGE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MergeFailed(f"gh pr merge did not run: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise MergeFailed(detail or f"gh pr merge exited {proc.returncode}")

    # `gh pr merge` prints little and has reported success for work it had not
    # finished; GitHub's own state is the answer.
    view = run_gh(
        ["pr", "view", str(pr.number), "--json", "state,mergeCommit"],
        cwd=repo_root,
        timeout=30,
    )
    if view.returncode != 0:
        raise MergeFailed(f"merged, but could not confirm it: {(view.stderr or '').strip()}")
    payload = json.loads(view.stdout or "{}")
    if payload.get("state") != "MERGED":  # py-org: allow-string
        raise MergeFailed(f"gh reported success but #{pr.number} is {payload.get('state')!r}")
    return (payload.get("mergeCommit") or {}).get("oid", "")


def _is_dirty(path: str) -> bool:
    status = run_git(
        ["status", "--porcelain"], cwd=path, check=False, capture_output=True, text=True
    )
    if status.returncode != 0:
        raise ValueError((status.stderr or "git status failed").strip())
    return bool(status.stdout.strip())


def _remove_worktrees(
    session: Session, workspace: Workspace, repo_root: Path, branch: str
) -> list[MergeStep]:
    steps: list[MergeStep] = []
    primary = str(repo_root.resolve())
    for path in worktree_paths_for_branch(repo_root, branch):
        step = f"worktree {path}"
        if path == primary:
            steps.append(
                MergeStep(
                    step=step,
                    ok=False,
                    detail=f"the primary checkout is on {branch}; switch it to the base by hand",
                )
            )
            continue
        try:
            if _is_dirty(path):
                steps.append(
                    MergeStep(step=step, ok=False, detail="kept: it has uncommitted changes")
                )
                continue
            remove_branch_worktree(workspace, branch, path)
        except ValueError as exc:
            steps.append(MergeStep(step=step, ok=False, detail=str(exc)))
            continue
        _retire_worktree_rows(session, path)
        steps.append(MergeStep(step=step, ok=True, detail="removed"))
    return steps


def _retire_worktree_rows(session: Session, path: str) -> None:
    rows = session.exec(
        select(Worktree).where(
            Worktree.worktree_path == path, Worktree.state == WorktreeState.ACTIVE
        )
    ).all()
    for row in rows:
        row.state = WorktreeState.CLEANUP
        row.cleaned_at = utcnow()
        session.add(row)
    if rows:
        session.commit()


def _delete_local_branch(workspace: Workspace, branch: str) -> MergeStep:
    # -D, not -d: a squash merge leaves the branch's own commits off the base.
    try:
        deleted = delete_branch(workspace, branch, force=True)
    except ValueError as exc:
        return MergeStep(step="local branch", ok=False, detail=str(exc))
    return MergeStep(step="local branch", ok=True, detail="deleted" if deleted else "already gone")


def _delete_remote_branch(repo_root: Path, branch: str) -> MergeStep:
    # Through the API rather than `git push --delete`, which would run this
    # repository's whole pre-push suite to delete a ref.
    try:
        proc = run_gh(
            ["api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{branch}"],
            cwd=repo_root,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return MergeStep(step="remote branch", ok=False, detail=f"gh did not run: {exc}")
    if proc.returncode == 0:
        return MergeStep(step="remote branch", ok=True, detail="deleted")
    detail = (proc.stderr or proc.stdout or "").strip()
    if "Reference does not exist" in detail:
        return MergeStep(step="remote branch", ok=True, detail="already gone")
    return MergeStep(step="remote branch", ok=False, detail=detail or "gh api DELETE failed")
