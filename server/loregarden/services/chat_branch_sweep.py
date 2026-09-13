"""Reap the branches a chat thread leaves behind, once its work has landed.

Every acting turn commits and pushes to `chat/<thread>`, so a rail that acts
accumulates a branch per conversation — on disk, in `git branch`, and on the
remote — with nothing to remove them. `reconcile_worktrees` walks past a chat
worktree because its test is the *ticket's* state and a chat thread has no
ticket; branch triage's bulk cleanup clears them, but only when a person opens
the dialog and clicks.

The bar for deleting one unattended is the bar that dialog pre-selects on, and
it is the only bar at which deletion destroys nothing: every file the branch
touched is byte-identical between its tip and the base branch, so the content is
already in `main` and the ref was keeping nothing reachable that is not reachable
without it. Anything short of that — a dirty tree, work not yet landed, a base
that cannot be resolved — is left exactly where it is and stays visible in branch
triage for a person to judge.

Startup only, for the reason `reconciliation` gives about `reconcile_worktrees`:
this deletes, and boot is the only moment we know nothing is in flight. Off
unless a workspace opts in with `git.prune_landed_chat_branches`, because
removing refs from someone's remote is not something to infer from `push`.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.models.domain import Workspace, Worktree, WorktreeState
from loregarden.services.artifact_service import git_base_ref
from loregarden.services.branch_triage_service import branch_work_has_landed
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_lifecycle import retire_worktree
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: How long one remote deletion may take before the sweep gives up on it. Sized
#: for a slow round trip rather than a stalled one: a branch the sweep cannot
#: clear now is cleared on the next boot, and holding startup open for a remote
#: that is not answering costs every other thing waiting behind it.
_REMOTE_DELETE_TIMEOUT_SECONDS = 20


@dataclass
class ChatBranchSweep:
    """What the sweep removed, and what it deliberately did not.

    `kept` carries a reason per branch rather than a count. A sweep that reports
    only what it deleted cannot be told apart from one whose predicate is broken
    and is quietly deleting nothing — and that is the failure mode that survives
    longest, because its symptom is the absence of a symptom.
    """

    removed: list[str] = field(default_factory=list)
    #: `(branch, why)` for every chat branch the sweep chose to leave alone.
    kept: list[tuple[str, str]] = field(default_factory=list)
    #: `(branch, why)` where the local ref went but the remote one would not.
    remote_failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def examined(self) -> int:
        return len(self.removed) + len(self.kept)


def _remote_for_branch(repo_root: Path, branch: str) -> str:
    """The remote this branch was pushed to, or "" when it never was."""
    configured = run_git(
        ["config", "--get", f"branch.{branch}.remote"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    return configured.stdout.strip() if configured.returncode == 0 else ""


def _delete_remote_branch(repo_root: Path, remote: str, branch: str) -> str:
    """Remove `branch` from `remote`. Returns "" on success, else the reason.

    Bounded, because this is the only step here that touches the network and it
    runs during startup: an unreachable remote would otherwise hold the boot
    open for git's own retry behaviour, once per branch. A timeout is reported
    like any other refusal — the local ref still goes, since its content landed
    either way, and the remote branch stays visible in branch triage.
    """
    try:
        removed = run_git(
            ["push", remote, "--delete", branch],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
            timeout=_REMOTE_DELETE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"{remote} did not answer within {_REMOTE_DELETE_TIMEOUT_SECONDS}s"
    if removed.returncode == 0:
        return ""
    detail = ((removed.stderr or removed.stdout) or "git push --delete failed").strip()
    # Already gone upstream is the goal state, not a failure: someone clearing
    # the branch through GitHub is the ordinary way that happens.
    if "remote ref does not exist" in detail.lower():
        return ""
    return detail


def _active_chat_worktrees(session: Session, workspace_id: str) -> list[Worktree]:
    return list(
        session.exec(
            select(Worktree)
            .where(Worktree.workspace_id == workspace_id)
            .where(Worktree.chat_session_id.is_not(None))
            .where(Worktree.state == WorktreeState.ACTIVE)
        ).all()
    )


def sweep_chat_branches(session: Session, workspace: Workspace) -> ChatBranchSweep:
    """Clear every chat branch in `workspace` whose work is already in base."""
    result = ChatBranchSweep()
    config = resolve_git_automation(workspace)
    if not config.prune_landed_chat_branches:
        return result

    repo_root = resolve_workspace_root(workspace)
    base = git_base_ref(repo_root)
    if base is None:
        # Not "nothing to do": without a base there is no way to prove anything
        # landed, and the sweep has to say so rather than report a clean pass.
        logger.warning(
            "Cannot sweep chat branches in %s: no base ref to compare against", repo_root
        )
        return result

    service = WorktreeService(session, repo_path=str(repo_root))
    for worktree in _active_chat_worktrees(session, workspace.id):
        branch = worktree.branch
        if not branch:
            result.kept.append(("<unnamed>", "the worktree row records no branch"))
            continue
        if not branch_work_has_landed(repo_root, base, branch):
            result.kept.append((branch, f"work is not yet in {base}"))
            continue
        # `retire_worktree` applies the same refusal the ticket path does: a tree
        # holding uncommitted changes keeps its checkout, whatever the branch
        # looks like from outside.
        if not retire_worktree(session, service, worktree):
            result.kept.append((branch, "its worktree holds uncommitted work"))
            continue

        remote = _remote_for_branch(repo_root, branch)
        if remote:
            failure = _delete_remote_branch(repo_root, remote, branch)
            if failure:
                logger.warning("Could not clear %s from %s: %s", branch, remote, failure)
                result.remote_failures.append((branch, failure))

        dropped = run_git(
            ["branch", "-D", branch],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        if dropped.returncode != 0:
            detail = ((dropped.stderr or dropped.stdout) or "branch -D failed").strip()
            logger.warning("Could not drop local branch %s: %s", branch, detail)
            result.kept.append((branch, f"local ref would not drop: {detail}"))
            continue
        result.removed.append(branch)

    return result


def sweep_all_chat_branches(session: Session) -> int:
    """Sweep every workspace. Returns how many branches were removed.

    Best-effort per workspace, matching `reconciliation`: one repository that
    cannot be read must not stop the others from being swept.
    """
    removed = 0
    for workspace in session.exec(select(Workspace)).all():
        try:
            result = sweep_chat_branches(session, workspace)
        except Exception:
            logger.exception("Chat branch sweep failed for workspace %s", workspace.slug)
            continue
        removed += len(result.removed)
        if result.removed:
            logger.warning(
                "Swept %d landed chat branch(es) in %s: %s",
                len(result.removed),
                workspace.slug,
                ", ".join(result.removed),
            )
    return removed
