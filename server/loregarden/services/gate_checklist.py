"""Ticket-specific expansion of a human gate's checklist.

`expand_gate_checklist` in `core.workflow_loader` is pure: it turns placeholders
into items given the data. This module is the half that has to touch the
workspace — reading the ticket's branch diff to find the scenes a playtest gate
should tell the operator to open — and keeps that git work out of the core.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.core.workflow_loader import (
    PLAYTEST_SCENES_PLACEHOLDER,
    expand_gate_checklist,
)
from loregarden.models.domain import Ticket
from loregarden.services.artifact_service import git_base_ref
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.git_subprocess import run_git
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

#: Files a human can open and run directly. Godot scenes are the only such
#: entry point in the workspaces that use the placeholder today; a workspace
#: whose playable unit is something else adds its suffix here.
SCENE_SUFFIXES = (".tscn",)

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 20


def _git(repo_root: Path, *args: str):
    return run_git(
        ["-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )


def resolve_playtest_scenes(session: Session, ticket: Ticket) -> list[str] | None:
    """Scene files the ticket's branch changes, or None if that can't be read.

    None is not "no scenes" — it means the workspace repo, the branch, or the
    base ref was unavailable, and the caller must keep the play-the-scenes step
    in generic form rather than dropping it.
    """
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        return None
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return None

    branch = resolve_ticket_branch(ticket)
    if _git(repo_root, "rev-parse", "--verify", branch).returncode != 0:
        return None
    base = git_base_ref(repo_root)
    if base is None:
        return None

    proc = _git(repo_root, "diff", "--name-only", "--diff-filter=d", f"{base}...{branch}")
    if proc.returncode != 0:
        return None
    return _scene_files(proc.stdout or "")


def _scene_files(names: str) -> list[str]:
    return sorted(
        line.strip() for line in names.splitlines() if line.strip().endswith(SCENE_SUFFIXES)
    )


def resolve_review_scenes(session: Session, ticket: Ticket) -> list[str] | None:
    """Scenes a reviewer should open for ``ticket``, read from its own change set.

    A landed ticket's branch diff against the base goes empty once its
    integration branch reaches the base, which is exactly when a reviewer of
    the combined build still needs the scene list. A landing merge
    (``landed_sha`` with two parents) holds the ticket's change set on its own:
    ``landed_sha^1..landed_sha``. A fast-forward landing has no such boundary,
    so it falls back to the branch diff, as an unlanded ticket does.
    """
    workspace = workspace_for_ticket(session, ticket)
    if ticket.landed_sha and workspace is not None:
        repo_root = resolve_workspace_root(workspace)
        parents = _git(repo_root, "rev-list", "--parents", "-n", "1", ticket.landed_sha)
        if parents.returncode == 0 and len(parents.stdout.split()) == 3:
            proc = _git(
                repo_root,
                "diff",
                "--name-only",
                "--diff-filter=d",
                f"{ticket.landed_sha}^1",
                ticket.landed_sha,
            )
            if proc.returncode == 0:
                return _scene_files(proc.stdout or "")
            logger.warning(
                "could not diff landing %s of %s: %s",
                ticket.landed_sha[:10], ticket.external_id, proc.stderr.strip(),
            )  # fmt: skip
    return resolve_playtest_scenes(session, ticket)


def expand_gate_checklist_for_ticket(
    session: Session, ticket: Ticket, checklist: list[str]
) -> list[str]:
    """`expand_gate_checklist` with the workspace-dependent inputs resolved.

    The scene lookup shells out to git, so it only runs when the checklist
    actually carries the placeholder — which, after the write-path expansion, is
    never true on the read path.
    """
    scenes = None
    if any(item.strip() == PLAYTEST_SCENES_PLACEHOLDER for item in checklist):
        scenes = resolve_playtest_scenes(session, ticket)
    return expand_gate_checklist(ticket, checklist, scenes=scenes)
