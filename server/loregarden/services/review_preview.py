"""A running copy of the build under review, for pages a person opens in a browser.

Answers "where can I see this branch right now?" for the review page. A kit's
links are paths (`/work-items`), and a path means nothing until it is joined to
an instance that runs the build: a client whose checkout is that branch, or the
review worktree while it sits at the branch's tip.

Matching is by checkout, not by the instance's name or a label someone typed:
an instance's `worktree` label is the directory it was launched from, and git
says which ref each directory holds. The review worktree is detached, so it
matches by commit instead of by branch name.

Start preview launches loregarden's own `server` (snapshot database) and
`client` templates in that checkout. They are the only templates whose
parameters this code knows; another workspace's instances are started on the
Instances page, and this reports that rather than guessing at its templates.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from lore_eden.instances import (
    InstanceKind,
    InstanceManager,
    InstanceState,
    InstanceView,
    LaunchRequest,
    Worktree,
    list_worktrees,
)
from loregarden.models.domain import Workspace
from loregarden.services.local_instances import PROJECT, get_instance_manager
from loregarden.services.review_launcher import (
    LaunchError,
    LaunchPhase,
    checkout_review_worktree,
    resolve_ref_sha,
    review_launcher,
    review_worktree_path,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: A review build in one of these is using the review worktree.
_BUILD_ACTIVE = frozenset({LaunchPhase.PREPARING, LaunchPhase.RUNNING})

#: States that are, or are becoming, a page someone can open.
LIVE_STATES = frozenset({InstanceState.STARTING, InstanceState.READY})


class PreviewInstance(BaseModel):
    id: str
    url: str
    state: InstanceState
    worktree: str


class PreviewView(BaseModel):
    ref: str
    client: PreviewInstance | None = None
    server: PreviewInstance | None = None
    #: Whether Start preview can launch one here; when not, ``unavailable`` says why.
    can_start: bool
    unavailable: str = ""


class PreviewError(ValueError):
    """Start preview cannot run; the message says what to do instead."""


def _head_sha(path: Path) -> str:
    try:
        return resolve_ref_sha(path, "HEAD")
    except LaunchError:
        # A worktree directory that is gone or broken holds no build: it is
        # simply not a candidate, and the listing reports what is running.
        logger.warning("review worktree %s has no readable HEAD", path)
        return ""


def build_checkouts(
    workspace: Workspace, ref: str, *, worktrees: Callable[[Path], list[Worktree]] = list_worktrees
) -> set[str]:
    """Directories whose checkout is ``ref``: worktrees on the branch, and the
    review worktree when its detached HEAD is the branch's tip."""
    repo_root = resolve_workspace_root(workspace)
    paths = {str(tree.path) for tree in worktrees(repo_root) if tree.branch == ref}
    review = review_worktree_path(workspace)
    if review.exists():
        tip = resolve_ref_sha(repo_root, ref)
        if _head_sha(review) == tip:
            paths.add(str(review))
    return paths


def _pick(
    views: list[InstanceView], kind: InstanceKind, checkouts: set[str]
) -> PreviewInstance | None:
    matches = [
        view
        for view in views
        if view.kind == kind
        and view.state in LIVE_STATES
        and view.labels.get("worktree", "") in checkouts
    ]
    if not matches:
        return None
    # A ready one over one still starting; the newest among equals.
    best = max(matches, key=lambda v: (v.state == InstanceState.READY, v.started_at))
    return PreviewInstance(
        id=best.id, url=best.url, state=best.state, worktree=best.labels.get("worktree", "")
    )


def _unavailable(workspace: Workspace) -> str:
    if workspace.slug == PROJECT:
        return ""
    return (
        f"Start preview knows loregarden's own server and client only; start {workspace.slug}'s "
        "instance for this branch on the Instances page."
    )


def preview_status(
    workspace: Workspace, ref: str, *, manager: InstanceManager | None = None
) -> PreviewView:
    checkouts = build_checkouts(workspace, ref)
    views = (manager or get_instance_manager()).list(project=workspace.slug).instances
    reason = _unavailable(workspace)
    return PreviewView(
        ref=ref,
        client=_pick(views, InstanceKind.CLIENT, checkouts),
        server=_pick(views, InstanceKind.SERVER, checkouts),
        can_start=not reason,
        unavailable=reason,
    )


def _preview_checkout(workspace: Workspace, ref: str, manager: InstanceManager) -> Path:
    """A checkout of ``ref`` to launch from, moving the review worktree only if needed."""
    repo_root = resolve_workspace_root(workspace)
    on_branch = [tree.path for tree in list_worktrees(repo_root) if tree.branch == ref]
    if on_branch:
        return on_branch[0]
    review = review_worktree_path(workspace)
    build = review_launcher.status(workspace.slug)
    if build is not None and build.phase in _BUILD_ACTIVE:
        raise PreviewError(
            f"A review build of {build.ref} is running in {review}; stop it before previewing {ref}"
        )
    # Instances launched from the review worktree are about to have their files
    # swapped under them, and a server would keep serving the old code.
    for view in manager.list(project=workspace.slug).instances:
        if view.labels.get("worktree") == str(review) and view.state in LIVE_STATES:
            manager.stop(view.id)
    try:
        path, _sha = checkout_review_worktree(workspace, ref)
    except LaunchError as exc:
        raise PreviewError(str(exc)) from exc
    return path


def start_preview(
    workspace: Workspace, ref: str, *, manager: InstanceManager | None = None
) -> PreviewView:
    reason = _unavailable(workspace)
    if reason:
        raise PreviewError(reason)
    instances = manager or get_instance_manager()
    current = preview_status(workspace, ref, manager=instances)
    if current.client is not None:
        return current
    checkout = str(_preview_checkout(workspace, ref, instances))
    server = current.server
    if server is None:
        launched = instances.launch(
            LaunchRequest(
                template=f"{PROJECT}/server", params={"worktree": checkout, "database": "snapshot"}
            )
        )
        server_id = launched.id
    else:
        server_id = server.id
    instances.launch(
        LaunchRequest(
            template=f"{PROJECT}/client", params={"worktree": checkout, "target": server_id}
        )
    )
    return preview_status(workspace, ref, manager=instances)
