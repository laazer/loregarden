"""Let an agent merge the base into an integration branch that conflicts with it.

When `main` moves with a change that conflicts with a tree's integration
branch, every stage run in the tree refuses to start (the refresh in
`target_branch`). Before this, the only way forward was a person: the inbox
card in `integration_conflict_card` asked someone to do the merge by hand,
and until they did, an autopilot dispatching the tree's tickets watched each
one block within two seconds and paused itself (init-pre-tinkercg-3, after
#578/#583/#592 touched three files the tree had also changed).

A landing conflict already gets a resolution turn (`landing`), under the same
two switches this honours: `auto_resolve_conflicts`, and
`max_conflict_resolve_attempts` per integration tip. This is that turn for the
integration branch itself. It runs the `repair` agent in a throwaway worktree
detached at the branch's tip, mid-merge, and moves the branch only when the
result is a finished merge of both tips with no conflict left: an unmerged
path, a staged-but-uncommitted merge or a leftover marker refuses it. The move
is a compare-and-swap, so a sibling that landed meanwhile is never lost; the
next dispatch merges again.

The turn is recorded as an `AgentRun` on the tree's root, so the operator can
read what it did, and its failures are what the attempt budget counts. When
the switch is off, the budget is spent or the turn fails, the caller files the
card as before, naming why the agent did not resolve it.

Kept free of `target_branch` imports so `target_branch` can call it.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace, utcnow
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    run_cli_agent_turn,
    stub_response,
)
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_merge_noco import is_ancestor, read_branch_tips, rev
from loregarden.services.git_subprocess import run_git
from loregarden.services.run_concurrency import new_run_code
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: `AgentRun.stage_key` for these turns. Not a workflow stage: nothing advances on it.
INTEGRATION_MERGE_STAGE = "integration-merge"

INTEGRATION_MERGE_CLI_PROFILE = CliAgentProfile(
    agent_id=REPAIR_AGENT_ID,
    assistant_label="Integration merge",
    cli_label="Integration merge",
    stub_env="LOREGARDEN_INTEGRATION_MERGE_STUB_RESPONSE",
    timeout_env="LOREGARDEN_INTEGRATION_MERGE_TIMEOUT",
    tmp_prefix="loregarden-integration-merge-",
    reply_cap=8000,
)

#: Markers git writes into a conflicted file. `=======` alone is left out: it is
#: ordinary Markdown and reStructuredText.
_MARKER_PATTERN = r"^(<<<<<<<|>>>>>>>)( |$)"

_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class MergeResolution:
    """Whether ``branch`` now holds the base; ``detail`` says why not."""

    ok: bool
    detail: str = ""


def _lock_for(repo_root: Path, branch: str) -> threading.Lock:
    """One merge per branch at a time: two siblings dispatched together hit the
    same conflict, and the second should wait for the first's merge, not race it."""
    key = (str(repo_root.resolve()), branch)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _git(cwd: Path, *args: str):
    return run_git(list(args), cwd=str(cwd), check=False, capture_output=True, text=True)


def _words(result, fallback: str) -> str:
    return (result.stderr or result.stdout or fallback).strip()


def failed_attempts(session: Session, root: Ticket, *, branch_sha: str) -> int:
    """Failed merge turns on this exact integration tip.

    Keyed by the tip so a new landing on the branch, or a person's own merge,
    starts a fresh budget, while a base that keeps moving does not buy the same
    unmergeable tip endless turns.
    """
    return len(
        session.exec(
            select(AgentRun.id).where(
                AgentRun.ticket_id == root.id,
                AgentRun.stage_key == INTEGRATION_MERGE_STAGE,
                AgentRun.start_head_sha == branch_sha,
                AgentRun.status == RunStatus.FAILED,
            )
        ).all()
    )


def merge_base_by_agent(
    session: Session,
    workspace: Workspace,
    root: Ticket,
    *,
    repo_root: Path,
    branch: str,
    base: str,
    files: tuple[str, ...],
) -> MergeResolution:
    """Have the repair agent merge ``base`` into ``branch``. Blocks while it runs.

    ``ok`` means the caller's refresh can be retried: the branch was merged
    here, or by a concurrent caller while this one waited for the lock.
    """
    automation = resolve_git_automation(workspace, root)
    if not automation.auto_resolve_conflicts:
        return MergeResolution(False, "auto_resolve_conflicts is off for this tree")
    with _lock_for(repo_root, branch):
        tips = read_branch_tips(repo_root, branch, base)
        branch_sha, base_sha = tips.of(branch), tips.of(base)
        if not branch_sha or not base_sha:
            return MergeResolution(False, tips.error or f"cannot read {branch} and {base}")
        if is_ancestor(repo_root, base_sha, branch_sha):
            return MergeResolution(True, "already merged by a concurrent turn")
        spent = failed_attempts(session, root, branch_sha=branch_sha)
        if spent >= automation.max_conflict_resolve_attempts:
            return MergeResolution(
                False,
                f"{spent} automatic merge attempt(s) on {branch_sha[:12]} already failed "
                f"(max_conflict_resolve_attempts={automation.max_conflict_resolve_attempts})",
            )
        run = _start_run(session, workspace, root, branch=branch, branch_sha=branch_sha)
        logger.warning(
            "Integration branch %s cannot take %s (conflicts in %s); running merge turn %s",
            branch,
            base,
            ", ".join(files),
            run.run_code,
        )
        resolution, reply = _merge_in_worktree(
            workspace,
            root,
            run,
            repo_root=repo_root,
            branch=branch,
            base=base,
            tips=(branch_sha, base_sha),
            files=files,
        )
        _finish_run(session, run, resolution, reply)
        return resolution


def _start_run(
    session: Session, workspace: Workspace, root: Ticket, *, branch: str, branch_sha: str
) -> AgentRun:
    run = AgentRun(
        run_code=new_run_code(),
        ticket_id=root.id,
        workspace_id=workspace.id,
        agent_id=REPAIR_AGENT_ID,
        stage_key=INTEGRATION_MERGE_STAGE,
        status=RunStatus.RUNNING,
        started_at=utcnow(),
        start_branch=branch,
        start_head_sha=branch_sha,
        auto_approve=True,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _finish_run(session: Session, run: AgentRun, resolution: MergeResolution, reply: str) -> None:
    run.status = RunStatus.SUCCEEDED if resolution.ok else RunStatus.FAILED
    run.stdout = reply
    run.stderr = "" if resolution.ok else resolution.detail[:4000]
    run.finished_at = utcnow()
    session.add(run)
    session.commit()
    if resolution.ok:
        logger.info("Merge turn %s resolved %s", run.run_code, run.start_branch)
    else:
        logger.warning(
            "Merge turn %s left %s unresolved: %s",
            run.run_code,
            run.start_branch,
            resolution.detail,
        )


def _merge_in_worktree(
    workspace: Workspace,
    root: Ticket,
    run: AgentRun,
    *,
    repo_root: Path,
    branch: str,
    base: str,
    tips: tuple[str, str],
    files: tuple[str, ...],
) -> tuple[MergeResolution, str]:
    branch_sha, base_sha = tips
    holder = Path(tempfile.mkdtemp(prefix="loregarden-integration-merge-"))
    tree = holder / "tree"
    added = _git(repo_root, "worktree", "add", "--detach", str(tree), branch_sha)
    if added.returncode != 0:
        shutil.rmtree(holder, ignore_errors=True)
        return MergeResolution(
            False, f"cannot cut a worktree: {_words(added, 'git worktree add')}"
        ), ""
    try:
        merged = _git(
            tree, "merge", "--no-ff", "--no-edit", "-m", f"Merge {base} into {branch}", base_sha
        )
        agent_error = ""
        if merged.returncode == 0:
            reply = "git merged cleanly in a checkout; no agent turn was needed."
        else:
            # Exit 1 with conflicts is the expected start; the agent finishes it.
            try:
                reply = _run_agent(
                    workspace,
                    root,
                    run,
                    tree=tree,
                    branch=branch,
                    base=base,
                    tips=tips,
                    files=files,
                )
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                # Not final on its own: a turn that committed the merge and then
                # timed out or replied with nothing still did the work, and the
                # tree, not the reply, is what is checked below.
                logger.warning("Merge turn %s for %s did not finish: %s", run.run_code, branch, exc)
                agent_error, reply = str(exc), ""
        verdict = _verify(tree, branch_sha=branch_sha, base_sha=base_sha, files=files)
        if not verdict.ok:
            if agent_error:
                return MergeResolution(
                    False, f"the merge turn failed ({agent_error}); {verdict.detail}"
                ), reply
            return verdict, reply
        return _move_branch(repo_root, tree, branch=branch, expected=branch_sha), reply
    finally:
        _remove_worktree(repo_root, tree)
        shutil.rmtree(holder, ignore_errors=True)


def _run_agent(
    workspace: Workspace,
    root: Ticket,
    run: AgentRun,
    *,
    tree: Path,
    branch: str,
    base: str,
    tips: tuple[str, str],
    files: tuple[str, ...],
) -> str:
    stub = stub_response(INTEGRATION_MERGE_CLI_PROFILE)
    if stub is not None:
        return stub
    return run_cli_agent_turn(
        INTEGRATION_MERGE_CLI_PROFILE,
        workspace=workspace,
        prompt=merge_brief(root, branch=branch, base=base, tips=tips, files=files),
        run_id=run.id,
        workspace_slug=workspace.slug or "",
        workspace_root=tree,
    )


def merge_brief(
    root: Ticket, *, branch: str, base: str, tips: tuple[str, str], files: tuple[str, ...]
) -> str:
    branch_sha, base_sha = tips
    listed = "\n".join(f"- {path}" for path in files) or "- (git named no paths; run `git status`)"
    return (
        f"# Merge `{base}` into `{branch}`\n\n"
        f"`{branch}` is the integration branch of {root.external_id} ({root.title}): every "
        f"ticket in that tree lands on it, and none can start while it cannot take `{base}`.\n\n"
        f"This working tree is detached at `{branch}` ({branch_sha[:12]}) and mid-merge with "
        f"`{base}` ({base_sha[:12]}). git stopped on conflicts in:\n{listed}\n\n"
        "## Do\n"
        f"1. For each file, read what each side meant: `git log --oneline HEAD...MERGE_HEAD -- <file>` "
        "and the commits it lists.\n"
        f"2. Resolve every conflict so both intents survive. `{base}` holds work that already "
        f"shipped; `{branch}` holds this tree's landed tickets. Do not drop either side to make "
        "the conflict go away.\n"
        "3. Run the tests that cover the files you resolved, and fix what the merge broke.\n"
        "4. `git add` the resolved files and `git commit --no-edit` to finish the merge.\n\n"
        "## Do not\n"
        "- push, switch branches, rebase, reset, or abort the merge;\n"
        "- edit files the merge did not touch, beyond what a broken test needs.\n\n"
        "If a conflict needs a decision only a person can make, leave the merge uncommitted and "
        "say why in your reply: the branch is not moved, and a person is asked instead."
    )


def _verify(
    tree: Path, *, branch_sha: str, base_sha: str, files: tuple[str, ...]
) -> MergeResolution:
    """A finished merge of both tips, with nothing left half-resolved."""
    if rev(tree, "MERGE_HEAD"):
        return MergeResolution(False, "the merge turn ended without committing the merge")
    status = _git(tree, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        return MergeResolution(False, f"cannot read the merge tree: {_words(status, 'git status')}")
    if status.stdout.strip():
        return MergeResolution(
            False, f"the merge turn left uncommitted changes:\n{status.stdout.strip()[:1500]}"
        )
    head = rev(tree, "HEAD")
    if not (is_ancestor(tree, branch_sha, head) and is_ancestor(tree, base_sha, head)):
        return MergeResolution(
            False,
            f"HEAD {head[:12] or '?'} does not contain both {branch_sha[:12]} and {base_sha[:12]}",
        )
    if files:
        markers = _git(tree, "grep", "-n", "-E", _MARKER_PATTERN, "HEAD", "--", *files)
        if markers.returncode == 0:
            return MergeResolution(
                False, f"conflict markers were committed:\n{markers.stdout.strip()[:1500]}"
            )
        if markers.returncode != 1:
            return MergeResolution(
                False, f"cannot check for markers: {_words(markers, 'git grep')}"
            )
    return MergeResolution(True)


def _move_branch(repo_root: Path, tree: Path, *, branch: str, expected: str) -> MergeResolution:
    head = rev(tree, "HEAD")
    moved = _git(repo_root, "update-ref", f"refs/heads/{branch}", head, expected)
    if moved.returncode != 0:
        # A sibling landed while the turn ran. Its work is kept; the next
        # dispatch finds the branch still behind the base and merges again.
        return MergeResolution(
            False,
            f"{branch} moved while the merge ran, so {head[:12]} was not applied: "
            f"{_words(moved, 'git update-ref refused')}",
        )
    logger.warning("Merged the base into %s as %s (was %s)", branch, head[:12], expected[:12])
    return MergeResolution(True)


def _remove_worktree(repo_root: Path, tree: Path) -> None:
    removed = _git(repo_root, "worktree", "remove", "--force", str(tree))
    if removed.returncode != 0:
        logger.warning(
            "Could not remove merge worktree %s: %s; `git worktree prune` clears it once the "
            "directory is gone",
            tree,
            _words(removed, "git worktree remove"),
        )
