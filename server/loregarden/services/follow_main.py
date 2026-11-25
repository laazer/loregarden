"""The dev server follows main: what the merge queue lands, the server runs.

The merge queue lands loregarden's own fixes, and the server running it kept
serving the build it booted with until someone pulled the primary checkout and
reloaded by hand. A fix to the queue itself waited on a person before it could
act, and #932's lore-eden bump sat in main while the server ran the old pin.

On each CI poll tick, for the server's own checkout only, `follow_main`:

1. leaves alone a checkout that is on another branch or has commits main lacks
   (only a person can decide what those mean);
2. fetches main and fast-forwards the checkout onto it with `git merge
   --ff-only`, which refuses rather than overwrite uncommitted changes to a file
   main changed. The post-merge hook (`.lefthook/scripts/sync-deps.sh`) syncs
   the venv and node_modules when a lockfile moved;
3. asks the dev-server supervisor to reload when anything under `server/`
   differs from the build it serves. The supervisor imports the new build
   first and keeps the old one serving if that fails, which the doctor's
   `dev_server_reload` check reports.

A pull that changes `server/` and every reload wait until no agent run, stage
or orchestration is in flight, so neither the venv nor the process changes under
running work. One that has waited a while holds new work back, for a limited time,
so that moment comes (`reload_hold`). Each outcome is written beside the supervisor's status and read
by the doctor's `server_follows_main` check; a stuck one is also a tick error,
which the CI poll logs when it starts and when it clears.

Off unless `settings.follow_main` (set by `scripts/dev-server.sh`), never in a
sandbox, and only for the `loregarden` workspace whose root is the server's own
checkout: a worktree's server, a test, or another workspace is never moved.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from loregarden.config import settings
from loregarden.dev_supervisor import STATE_DIR
from loregarden.models.domain import FollowMainState, Workspace
from loregarden.services import reload_hold
from loregarden.services.follow_main_status import (
    FOLLOW_FILE,
    FollowStatus,
)
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_subprocess import run_git
from loregarden.services.merge_queue_loop import fetch_base
from loregarden.services.self_improve_restart import (
    SELF_IMPROVE_WORKSPACE,
    evaluate_reload_readiness,
    touch_reload_sentinel,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: The tree whose change means the running process is stale.
SERVER_TREE = "server/"

#: Outcomes that leave the hold as it is.
_HOLD_KEPT = frozenset(
    {FollowMainState.WAITING_FOR_IDLE, FollowMainState.HOLDING, FollowMainState.GIT_FAILED}
)


class _GitError(RuntimeError):
    """A git read this needs failed; recorded as `GIT_FAILED`."""


@dataclass
class _RunningBuild:
    """This process's view of the build it serves, and what it has asked for."""

    #: HEAD when this worker booted: the code it is running.
    head: str = ""
    #: The head a reload was last requested for, so a refused build is asked once.
    reload_requested_for: str = ""


_running = _RunningBuild()


def remember_running_build(repo_root: Path | None = None) -> None:
    """Record the checkout's HEAD as the build this worker serves. Call at boot."""
    root = repo_root or settings.repo_root
    try:
        _running.head = _git(root, "rev-parse", "HEAD")
    except _GitError:
        # Unknown, so never reload on a guess; follow_main still pulls and says so.
        logger.warning("follow-main: cannot read the running build's HEAD in %s", root)
        _running.head = ""
    _running.reload_requested_for = ""


def is_server_checkout(workspace: Workspace) -> bool:
    """Is ``workspace`` the checkout this server runs from, with following on?"""
    if not settings.follow_main or settings.sandbox:
        return False
    if workspace.slug != SELF_IMPROVE_WORKSPACE:
        return False
    return resolve_workspace_root(workspace).resolve() == settings.repo_root.resolve()


def follow_main(session: Session, workspace: Workspace) -> FollowStatus:
    """One tick: fast-forward onto main and reload when the server's code moved."""
    repo = settings.repo_root
    asked_before = _running.reload_requested_for
    try:
        status = _follow(session, workspace, repo)
    except _GitError as exc:
        status = FollowStatus(FollowMainState.GIT_FAILED, str(exc))
    _settle_hold(status, reload_requested=_running.reload_requested_for != asked_before)
    status = FollowStatus(
        status.state,
        status.detail,
        head=status.head,
        at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _record(repo, status)
    return status


def _follow(session: Session, workspace: Workspace, repo: Path) -> FollowStatus:
    base = base_branch(workspace)
    branch = _current_branch(repo)
    if branch != base:
        where = f"on {branch}" if branch else "on a detached HEAD"
        return FollowStatus(
            FollowMainState.OFF_BASE,
            f"the server's checkout is {where}, not {base}, so it does not follow {base}",
        )
    tip, failure = fetch_base(repo, base)
    if failure:
        raise _GitError(failure)
    head = _git(repo, "rev-parse", "HEAD")
    pulled = False
    if head != tip:
        ahead = int(_git(repo, "rev-list", "--count", f"{tip}..{head}"))
        if ahead:
            return FollowStatus(
                FollowMainState.DIVERGED,
                f"{base} in the server's checkout has {ahead} commit(s) origin/{base} does not",
                head=head,
            )
        if _changes_server(repo, head, tip) and (
            waiting := _waiting(session, workspace, f"{base} moved to {tip[:12]}", head)
        ):
            return waiting
        merged = run_git(
            ["merge", "--ff-only", "--quiet", tip],
            cwd=str(repo),
            check=False,
            capture_output=True,
            text=True,
        )
        if merged.returncode != 0:
            why = (merged.stderr or merged.stdout or "git merge --ff-only failed").strip()
            return FollowStatus(
                FollowMainState.REFUSED,
                f"could not fast-forward onto {tip[:12]}: {why}",
                head=head,
            )
        logger.info("follow-main: fast-forwarded %s from %s to %s", base, head[:12], tip[:12])
        head, pulled = tip, True
    return _reload_if_stale(session, workspace, repo, head, pulled=pulled)


def _reload_if_stale(
    session: Session, workspace: Workspace, repo: Path, head: str, *, pulled: bool
) -> FollowStatus:
    settled = FollowMainState.PULLED if pulled else FollowMainState.CURRENT
    running = _running.head
    if not running:
        return FollowStatus(settled, "the running build is unknown, so it was not reloaded", head)
    if running == head or not _changes_server(repo, running, head):
        return FollowStatus(settled, f"serving {head[:12]}", head)
    if _running.reload_requested_for == head:
        return FollowStatus(
            FollowMainState.RELOADING,
            f"reload onto {head[:12]} requested; the supervisor's outcome is the "
            "dev_server_reload check",
            head,
        )
    if waiting := _waiting(
        session, workspace, f"the server runs {running[:12]}, not {head[:12]}", head
    ):
        return waiting
    touch_reload_sentinel()
    _running.reload_requested_for = head
    logger.info("follow-main: reloading from %s onto %s", running[:12], head[:12])
    return FollowStatus(FollowMainState.RELOADING, f"reloading onto {head[:12]}", head)


def _waiting(session: Session, workspace: Workspace, what: str, head: str) -> FollowStatus | None:
    """What this tick waits for, starting or keeping a hold; None when nothing is in flight."""
    in_flight = evaluate_reload_readiness(session, workspace_slug=workspace.slug)
    if not in_flight["blockers"]:
        return None
    hold = reload_hold.note_waiting(in_flight["cannot_yield"])
    detail = f"{what}; waiting for in-flight work to finish ({', '.join(in_flight['blockers'])})"
    return FollowStatus(
        FollowMainState.HOLDING if hold.holding else FollowMainState.WAITING_FOR_IDLE,
        f"{detail}; {hold.detail}" if hold.detail else detail,
        head,
    )


def _settle_hold(status: FollowStatus, *, reload_requested: bool) -> None:
    """End the hold once nothing waits on it.

    Kept on the tick that asks for the reload, so nothing starts in the seconds
    before the process goes: a later tick still asking means the supervisor
    refused the build. Kept over a failed git read, which says nothing about
    what is waiting; the hold's own deadline bounds that.
    """
    if status.state in _HOLD_KEPT or reload_requested:
        return
    reload_hold.note_settled()


def _current_branch(repo: Path) -> str:
    """The checked-out branch, or "" on a detached HEAD."""
    result = run_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 1:
        return ""
    if result.returncode != 0:
        raise _GitError(f"cannot read the server checkout's branch: {result.stderr.strip()}")
    return result.stdout.strip()


def _changes_server(repo: Path, old: str, new: str) -> bool:
    result = run_git(
        ["diff", "--quiet", old, new, "--", SERVER_TREE],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode > 1:
        raise _GitError(f"cannot compare {old[:12]}..{new[:12]}: {result.stderr.strip()}")
    return result.returncode == 1


def _git(repo: Path, *args: str) -> str:
    result = run_git(list(args), cwd=str(repo), check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise _GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _record(repo: Path, status: FollowStatus) -> None:
    """Write the outcome for the doctor. A stuck one is logged by the CI poll's report."""
    path = repo / STATE_DIR / FOLLOW_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(status)), encoding="utf-8")
    except OSError:
        logger.exception("follow-main: cannot write %s; the doctor will not see this tick", path)
