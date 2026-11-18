"""The ticket's own work still uncommitted in its tree, which landing must not leave behind.

A landing merges the branch's commits, so anything left only in the worktree
does not land, and the ticket still derives done. lg-milestone-that-1237's
implement stage edited `handoff_tree_status.py`, never committed it, and
landed its tests alone; four tests went red on the integration branch until
lg-milestone-that-1314 carried the edit across by hand. On 2026-10-21 eight
landed loregarden tickets still had tracked edits sitting in their trees, each
made before the ticket landed.

The tree is the ticket's own, so every tracked edit in it is the ticket's.
Recorded paths do not narrow tracked edits. They did, through the handoff
check's rule (dirty ∩ recorded once anything was recorded), and
lg-code-knowledge-1055's rework round edited three tracked files no earlier
round had recorded: the stage-end commit skipped them, landing passed them, and
land verification tested a branch without them, four times.

Untracked files are mostly generated output (one blobert tree held fifteen
baked textures), and blocking every landing on them would stop more work than
the defect does. So an untracked file counts when the ticket's runs recorded
it, or when a file the ticket's tree changed against its base refers to it by
name. 1055's rework also added `asserted_claims.py`, unrecorded, which three of
its edits imported: committing the edits without it lands an import of a
module that is not there. Output nothing points at stays exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_subprocess import run_git
from loregarden.services.recorded_paths import ticket_recorded_paths
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_snapshot import read_tree
from sqlmodel import Session

#: A file stem that names its directory rather than itself (`pkg/__init__.py` is `pkg`).
_PACKAGE_STEMS = frozenset({"__init__", "index", "mod", "main"})


def _name(path: str) -> str:
    """What other files call ``path``: its stem, or its directory's name for a package file."""
    parts = Path(path)
    if parts.stem in _PACKAGE_STEMS and parts.parent.name:
        return parts.parent.name
    return parts.stem


def _git_out(root: Path, args: list[str], *, ok: tuple[int, ...] = (0,)) -> str:
    done = run_git(args, cwd=str(root), check=False, capture_output=True, text=True)
    if done.returncode not in ok:
        raise RuntimeError(
            f"git {args[0]} failed in {root}: {(done.stderr or done.stdout).strip()}"
        )
    return done.stdout


def _untracked(root: Path) -> set[str]:
    listed = _git_out(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    return {path for path in listed.split("\0") if path}


def _referenced(root: Path, untracked: list[str], base: str) -> set[str]:
    """The ``untracked`` paths that a file this tree changed against ``base`` refers to.

    A reference is how an import names a module: the file's name after a ``.`` or
    ``/``, with no extension and nothing more after it
    (`from loregarden.services.operations.asserted_claims import`,
    `import "./asserted_claims"`). A name spelled with its extension is code
    writing or listing a file, not depending on it: a build script's
    `out/report.json`, or a blobert handoff listing a stray `data/loregarden.db`
    among its dirty paths (the one hit across 78 live trees when the rule
    matched any mention). A Godot resource path spells its extension and is
    still an import: `preload("res://scripts/claims.gd")`, or a scene's
    `ext_resource`. So a file under a `project.godot` also counts when a changed
    file names it by its exact `res://` path from that project's root (blobert
    gitignores its generated textures and `.import` files, so an untracked file
    a scene names is one the landing would leave the scene without). A C
    `#include` still counts only when recorded. The files searched are the tracked ones changed since
    the merge base, committed or not, since the stage-end commit may already
    have committed the file that imports it.
    """
    fork = _git_out(root, ["merge-base", "HEAD", f"refs/heads/{base}"]).strip()
    changed = [p for p in _git_out(root, ["diff", "--name-only", "-z", fork]).split("\0") if p]
    if not changed:
        return set()
    names = {path: _name(path) for path in untracked}
    resources = {path: res for path in untracked if (res := _resource_path(root, path))}
    needles = {sep + name for name in names.values() for sep in "./"} | set(resources.values())
    patterns = [arg for needle in sorted(needles) for arg in ("-e", needle)]
    lines = _git_out(root, ["grep", "-h", "-I", "-F", *patterns, "--", *changed], ok=(0, 1))
    imported = {
        path for path, name in names.items() if re.search(rf"[./]{re.escape(name)}(?![\w.])", lines)
    }
    loaded = {
        path for path, res in resources.items() if re.search(rf"{re.escape(res)}(?![\w./-])", lines)
    }
    return imported | loaded


def _resource_path(root: Path, path: str) -> str | None:
    """``path`` as Godot names it (`res://…` from the nearest `project.godot` above it), or None."""
    relative = Path(path)
    for project in relative.parents:
        if (root / project / "project.godot").is_file():
            return f"res://{relative.relative_to(project).as_posix()}"
    return None


def uncommitted_at_landing(
    session: Session, ticket: Ticket, workspace: Workspace
) -> tuple[str, ...]:
    """Paths of this ticket's work that exist only in its worktree; () when none.

    A ticket with no worktree of its own runs in the shared checkout, where
    whose dirt it is cannot be told, so it is not judged here (as the handoff
    check does not block on it). Raises ``RuntimeError`` when git cannot list
    the tree: "could not tell" must not read as "nothing uncommitted".
    """
    root = resolve_ticket_root(session, ticket, workspace)
    if root == resolve_workspace_root(workspace) or not root.is_dir():
        return ()
    dirty = read_tree(root).dirty_paths
    if dirty is None:
        raise RuntimeError(f"git status failed in {root}")
    if not dirty:
        return ()
    untracked = _untracked(root)
    recorded = ticket_recorded_paths(session, ticket)
    unclaimed = sorted(path for path in dirty & untracked if path not in recorded)
    needed = _referenced(root, unclaimed, base_branch(workspace)) if unclaimed else set()
    return tuple(
        sorted(
            path for path in dirty if path not in untracked or path in recorded or path in needed
        )
    )


def describe_uncommitted(paths: tuple[str, ...], root_hint: str) -> str:
    """The sentence a blocked landing, or a finding, carries."""
    listed = ", ".join(paths[:10]) + (f" and {len(paths) - 10} more" if len(paths) > 10 else "")
    return (
        f"Not landed: {len(paths)} file(s) of this ticket's work are uncommitted in {root_hint} "
        f"({listed}). The landing merges commits only, so they would be left behind. Commit "
        "them to the ticket's branch, or discard them with `git checkout -- <path>` if they are "
        "not the ticket's work, then finish the ticket again."
    )
