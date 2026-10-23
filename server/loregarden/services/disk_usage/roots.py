"""What the disk scan covers, and which directories in it are git worktrees.

Three sources, deduplicated so no byte is counted twice:

- every registered, unarchived workspace's checkout;
- worktrees of those repositories that live *outside* their checkout (ticket
  and chat worktrees sit in a sibling `.worktrees/`, which held 25 GB across 50
  trees when this was written). Two or more under one parent scan that parent,
  so a stray directory nothing tracks shows up beside the trees that are;
- folders a person added (`DiskScanRoot`).

A root inside another root is dropped: the outer walk already counts it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import DiskRootOrigin, DiskScanRoot, Workspace
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorktreeRef:
    """A checkout `git worktree list` reported, and the repository it belongs to."""

    path: Path
    repo_root: Path
    branch: str
    is_main: bool
    workspace_slug: str


@dataclass(frozen=True)
class ScanRoot:
    path: Path
    label: str
    origin: DiskRootOrigin
    workspace_slug: str = ""


@dataclass(frozen=True)
class ScanPlan:
    roots: list[ScanRoot]
    worktrees: dict[Path, WorktreeRef]
    workspace_roots: frozenset[Path]
    #: Workspaces whose worktree list could not be read: their linked
    #: worktrees are missing from the picture, and the page says so.
    unreadable_workspaces: list[str]
    #: Added folders that are no longer directories; listed, never dropped quietly.
    missing_added: list[str]


def list_worktrees(repo_root: Path, slug: str) -> list[WorktreeRef] | None:
    """Every checkout of `repo_root`, or None when git could not say."""
    proc = run_git(
        ["worktree", "list", "--porcelain"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        logger.warning(
            "git worktree list failed in %s (%s): %s",
            repo_root,
            proc.returncode,
            (proc.stderr or "").strip(),
        )
        return None
    refs: list[WorktreeRef] = []
    path: Path | None = None
    branch = ""
    first = True
    for line in [*proc.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree ")).resolve()
            branch = ""
        elif line.startswith("branch "):
            branch = line.removeprefix("branch ").removeprefix("refs/heads/")
        elif not line and path is not None:
            # `git worktree list` always names the main working tree first.
            refs.append(WorktreeRef(path, repo_root, branch, first, slug))
            first = False
            path = None
    return refs


def _contains(outer: Path, inner: Path) -> bool:
    return inner == outer or inner.is_relative_to(outer)


def _dedupe(roots: list[ScanRoot]) -> list[ScanRoot]:
    """Drop every root another root contains; the first spelling of a path wins."""
    kept: list[ScanRoot] = []
    for root in sorted(roots, key=lambda r: len(r.path.parts)):
        if any(_contains(other.path, root.path) for other in kept):
            continue
        kept.append(root)
    return kept


def added_roots(session: Session) -> list[DiskScanRoot]:
    return list(session.exec(select(DiskScanRoot).order_by(DiskScanRoot.added_at)).all())


def _external_worktree_roots(
    refs: list[WorktreeRef], workspace_roots: list[Path], slug: str
) -> list[ScanRoot]:
    outside = [
        ref
        for ref in refs
        if not ref.is_main and not any(_contains(root, ref.path) for root in workspace_roots)
    ]
    by_parent: dict[Path, list[WorktreeRef]] = defaultdict(list)
    for ref in outside:
        by_parent[ref.path.parent].append(ref)
    roots: list[ScanRoot] = []
    for parent, members in by_parent.items():
        # The parent is scanned only when it is a worktree directory, not a
        # folder that also holds checkouts (worktrees beside the repo in
        # ~/workspace would otherwise scan every project there).
        if len(members) >= 2 and not any(_contains(parent, root) for root in workspace_roots):
            roots.append(ScanRoot(parent, f"{slug} worktrees", DiskRootOrigin.WORKTREES, slug))
        else:
            roots.extend(
                ScanRoot(
                    m.path,
                    f"{slug} worktree {m.branch or m.path.name}",
                    DiskRootOrigin.WORKTREES,
                    slug,
                )
                for m in members
            )
    return roots


def scan_plan(session: Session) -> ScanPlan:
    workspaces = session.exec(select(Workspace).where(Workspace.archived_at.is_(None))).all()
    roots: list[ScanRoot] = []
    worktrees: dict[Path, WorktreeRef] = {}
    unreadable: list[str] = []
    resolved: list[tuple[Workspace, Path]] = []
    for workspace in workspaces:
        root = resolve_workspace_root(workspace)
        if root.is_dir():
            resolved.append((workspace, root))
    workspace_roots = [root for _, root in resolved]
    for workspace, root in resolved:
        roots.append(
            ScanRoot(
                root, workspace.name or workspace.slug, DiskRootOrigin.WORKSPACE, workspace.slug
            )
        )
        refs = list_worktrees(root, workspace.slug) if (root / ".git").exists() else []
        if refs is None:
            unreadable.append(workspace.slug)
            continue
        for ref in refs:
            worktrees[ref.path] = ref
        roots.extend(_external_worktree_roots(refs, workspace_roots, workspace.slug))
    missing: list[str] = []
    for added in added_roots(session):
        path = Path(added.path)
        if path.is_dir():
            roots.append(ScanRoot(path, path.name or str(path), DiskRootOrigin.ADDED))
        else:
            missing.append(added.path)
    return ScanPlan(
        roots=_dedupe(roots),
        worktrees=worktrees,
        workspace_roots=frozenset(workspace_roots),
        unreadable_workspaces=unreadable,
        missing_added=missing,
    )
