"""One metadata-only walk of the scan roots into a pruned size tree.

What is counted, and why:

- **Allocated bytes** (`st_blocks * 512`), not `st_size`: a sparse file or an
  APFS clone reports a size it does not occupy, and the question is what the
  disk is holding.
- **A hard-linked inode once.** pnpm and uv link the same file into many trees;
  counting each link would claim space that freeing one link never returns.
- **Symlinks are never followed.** Ticket worktrees symlink the main checkout's
  `node_modules`; following it would count the same 400 MB fifty times.
- **One device per root.** A mounted volume inside a root is somebody else's disk.

Nothing is opened or read: `os.scandir` and `lstat` only. The MCP tool built on
this promises agents names and sizes and nothing else, and this walk is where
that promise is kept.

The tree is pruned as each directory finishes: children under `MIN_NODE_BYTES`,
and any past the `MAX_CHILDREN` largest, fold into one `other` node carrying
their total. The totals stay exact; only the detail is dropped.
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.models.domain import REGENERABLE_KINDS, DiskKind
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MIN_NODE_BYTES = 1024 * 1024
MAX_CHILDREN = 60
#: A walk past this many entries stops and says it is partial.
MAX_ENTRIES = 8_000_000
#: How many unreadable directories are named; the rest are only counted.
MAX_UNREADABLE_SAMPLES = 20
_PROGRESS_EVERY = 5_000

DEPENDENCY_NAMES = frozenset(
    {"node_modules", ".venv", "venv", "bower_components", "Pods", "site-packages"}
)
BUILD_OUTPUT_NAMES = frozenset(
    {
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".turbo",
        ".parcel-cache",
        "coverage",
        "htmlcov",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "DerivedData",
        ".gradle",
    }
)
CACHE_NAMES = frozenset({".cache", "Caches", ".npm", ".pnpm-store"})


class DiskNode(BaseModel):
    name: str
    path: str
    size_bytes: int
    kind: DiskKind
    is_dir: bool
    #: Files under this node, folded ones included.
    file_count: int = 0
    children: list[DiskNode] = Field(default_factory=list)
    #: On an `other` node: how many entries it stands for.
    folded_count: int = 0


def kind_for_name(name: str) -> DiskKind | None:
    if name in DEPENDENCY_NAMES:
        return DiskKind.DEPENDENCIES
    if name in BUILD_OUTPUT_NAMES:
        return DiskKind.BUILD_OUTPUT
    if name in CACHE_NAMES:
        return DiskKind.CACHE
    if name == ".git":
        return DiskKind.GIT_STORE
    return None


@dataclass
class ScanProgress:
    entries: int = 0
    current_root: str = ""
    unreadable: int = 0
    unreadable_samples: list[str] = field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class KindHints:
    """Paths whose kind is known from outside the walk."""

    worktrees: frozenset[Path]
    workspaces: frozenset[Path]
    loregarden_data: frozenset[Path]


@dataclass
class _Frame:
    path: Path
    name: str
    kind: DiskKind
    entries: list[os.DirEntry[str]]
    index: int = 0
    size: int = 0
    files: int = 0
    children: list[DiskNode] = field(default_factory=list)
    #: A `.git` *file* (not directory) marks a linked worktree or a submodule,
    #: whichever repository it belongs to, registered or not.
    git_file: bool = False


class ScanCancelled(Exception):
    pass


def _fold(children: list[DiskNode], parent: Path) -> list[DiskNode]:
    ordered = sorted(children, key=lambda n: n.size_bytes, reverse=True)
    kept = [n for n in ordered[:MAX_CHILDREN] if n.size_bytes >= MIN_NODE_BYTES]
    rest = ordered[len(kept) :]
    if not rest:
        return kept
    folded_size = sum(n.size_bytes for n in rest)
    if folded_size == 0:
        return kept
    kept.append(
        DiskNode(
            name=f"{len(rest)} smaller item{'s' if len(rest) != 1 else ''}",
            path=str(parent),
            size_bytes=folded_size,
            kind=DiskKind.OTHER,
            is_dir=False,
            file_count=sum(n.file_count for n in rest),
            folded_count=len(rest),
        )
    )
    return kept


def _finished(frame: _Frame) -> DiskNode:
    """A directory's node once every entry under it is counted."""
    kind = frame.kind
    if frame.git_file and kind is DiskKind.DIRECTORY:
        kind = DiskKind.WORKTREE
    return DiskNode(
        name=frame.name,
        path=str(frame.path),
        size_bytes=frame.size,
        kind=kind,
        is_dir=True,
        file_count=frame.files,
        children=_fold(frame.children, frame.path),
    )


class _Walker:
    def __init__(
        self,
        hints: KindHints,
        progress: ScanProgress,
        cancel: threading.Event,
        on_progress: Callable[[ScanProgress], None],
    ) -> None:
        self.hints = hints
        self.progress = progress
        self.cancel = cancel
        self.on_progress = on_progress
        self.seen_inodes: set[tuple[int, int]] = set()

    def _kind(self, path: Path, name: str, inherited: DiskKind) -> DiskKind:
        if inherited in REGENERABLE_KINDS:
            return inherited
        if path in self.hints.worktrees:
            return DiskKind.WORKTREE
        if path in self.hints.workspaces:
            return DiskKind.WORKSPACE
        if path in self.hints.loregarden_data:
            return DiskKind.LOREGARDEN_DATA
        return kind_for_name(name) or (
            inherited if inherited is DiskKind.GIT_STORE else DiskKind.DIRECTORY
        )

    def _allocated(self, st: os.stat_result) -> int:
        if st.st_nlink > 1:
            key = (st.st_dev, st.st_ino)
            if key in self.seen_inodes:
                return 0
            self.seen_inodes.add(key)
        return st.st_blocks * 512

    def _list(self, path: Path) -> list[os.DirEntry[str]]:
        try:
            with os.scandir(path) as it:
                return list(it)
        # silent-ok: not silent; `_unreadable` records it in the scan's unreadable
        # count and samples, which the Disk page and loregarden_disk_usage both report.
        except OSError as exc:
            self._unreadable(path, exc)
            return []

    def _unreadable(self, path: Path, exc: OSError) -> None:
        """Counted and sampled: the total is a lower bound, and the page says so."""
        self.progress.unreadable += 1
        if len(self.progress.unreadable_samples) < MAX_UNREADABLE_SAMPLES:
            self.progress.unreadable_samples.append(f"{path}: {exc.strerror or exc}")

    def _tick(self) -> None:
        self.progress.entries += 1
        if self.progress.entries % _PROGRESS_EVERY == 0:
            if self.cancel.is_set():
                raise ScanCancelled
            self.on_progress(self.progress)
        if self.progress.entries >= MAX_ENTRIES:
            self.progress.truncated = True

    def walk(self, root: Path, root_kind: DiskKind) -> DiskNode:
        device = root.lstat().st_dev
        stack = [_Frame(root, root.name or str(root), root_kind, self._list(root))]
        while True:
            frame = stack[-1]
            if frame.index >= len(frame.entries) or self.progress.truncated:
                stack.pop()
                node = _finished(frame)
                if not stack:
                    return node
                parent = stack[-1]
                parent.size += node.size_bytes
                parent.files += node.file_count
                parent.children.append(node)
                continue
            entry = frame.entries[frame.index]
            frame.index += 1
            self._tick()
            try:
                st = entry.stat(follow_symlinks=False)
            except (
                FileNotFoundError
            ):  # silent-ok: deleted between listing and stat; nothing left to count
                continue
            except OSError as exc:
                self._unreadable(frame.path / entry.name, exc)
                continue
            child_path = frame.path / entry.name
            if stat.S_ISDIR(st.st_mode):
                frame.size += st.st_blocks * 512
                if st.st_dev != device:
                    continue
                kind = self._kind(child_path, entry.name, frame.kind)
                stack.append(_Frame(child_path, entry.name, kind, self._list(child_path)))
                continue
            self._add_file(frame, entry.name, child_path, st)

    def _add_file(self, frame: _Frame, name: str, path: Path, st: os.stat_result) -> None:
        if name == ".git":
            frame.git_file = True
        size = self._allocated(st)
        frame.size += size
        frame.files += 1
        if size:
            file_kind = frame.kind if frame.kind in REGENERABLE_KINDS else DiskKind.FILE
            frame.children.append(
                DiskNode(
                    name=name,
                    path=str(path),
                    size_bytes=size,
                    kind=file_kind,
                    is_dir=False,
                    file_count=1,
                )
            )


def scan_roots(
    roots: list[tuple[Path, DiskKind]],
    hints: KindHints,
    *,
    progress: ScanProgress,
    cancel: threading.Event,
    on_progress: Callable[[ScanProgress], None],
) -> list[DiskNode]:
    """Walk each root; one shared inode set, so a link across roots counts once."""
    walker = _Walker(hints, progress, cancel, on_progress)
    nodes: list[DiskNode] = []
    for root, kind in roots:
        progress.current_root = str(root)
        on_progress(progress)
        nodes.append(walker.walk(root, kind))
        if progress.truncated:
            break
    return nodes
