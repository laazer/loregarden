"""Ticket-specific expansion of a human gate's checklist.

`expand_gate_checklist` in `core.workflow_loader` is pure: it turns placeholders
into items given the data. This module is the half that has to touch the
workspace — reading the ticket's branch diff to find the scenes a playtest gate
should tell the operator to open — and keeps that git work out of the core.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.core.workflow_loader import (
    PLAYTEST_SCENES_PLACEHOLDER,
    expand_gate_checklist,
)
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.artifact_service import git_base_ref
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

#: Files a human can open and run directly. Godot scenes are the only such
#: entry point in the workspaces that use the placeholder today; a workspace
#: whose playable unit is something else adds its suffix here.
SCENE_SUFFIXES = (".tscn",)

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
    workspace = session.get(Workspace, ticket.workspace_id)
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

    proc = _git(repo_root, "diff", "--name-only", f"{base}...{branch}")
    if proc.returncode != 0:
        return None
    return sorted(
        line.strip()
        for line in (proc.stdout or "").splitlines()
        if line.strip().endswith(SCENE_SUFFIXES)
    )


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
