"""The background scan, and the last finished tree.

One scan at a time, in a daemon thread: a walk of 60 GB takes tens of seconds
and no request should wait on it. The finished tree is written to
`data/disk-scan.json` (atomically), so it survives a restart and reaches the
CLI's in-process MCP path, which shares no memory with the server.

State a reader sees is one of: never scanned, running (with a live entry
count), finished, or failed with the error. "Failed" and "found nothing" are
different answers and stay different.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import DiskKind, DiskRootOrigin
from loregarden.models.domain.enums import utcnow
from loregarden.services.disk_usage.roots import ScanPlan, scan_plan
from loregarden.services.disk_usage.scanner import (
    DiskNode,
    KindHints,
    ScanCancelled,
    ScanProgress,
    scan_roots,
)
from loregarden.services.path_resolve import resolve_sqlite_path
from pydantic import BaseModel, Field
from sqlmodel import Session

logger = logging.getLogger(__name__)


class ScanState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class RootSummary(BaseModel):
    path: str
    label: str
    origin: DiskRootOrigin
    workspace_slug: str = ""


class ScanResult(BaseModel):
    """A finished scan, as stored."""

    started_at: datetime
    finished_at: datetime
    roots: list[RootSummary]
    tree: list[DiskNode]
    entries: int
    unreadable: int = 0
    unreadable_samples: list[str] = Field(default_factory=list)
    truncated: bool = False
    unreadable_workspaces: list[str] = Field(default_factory=list)
    missing_added: list[str] = Field(default_factory=list)


class ScanStatus(BaseModel):
    state: ScanState
    started_at: datetime | None = None
    entries: int = 0
    current_root: str = ""
    error: str = ""


def data_dir() -> Path:
    return resolve_sqlite_path(settings.database_url, settings.repo_root).parent


def database_path() -> Path:
    return resolve_sqlite_path(settings.database_url, settings.repo_root)


def _result_path() -> Path:
    return data_dir() / "disk-scan.json"


class _Job:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = ScanStatus(state=ScanState.IDLE)
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.cached: ScanResult | None = None
        self.cached_mtime: float | None = None


_JOB = _Job()


def _root_kind(origin: DiskRootOrigin) -> DiskKind:
    if origin is DiskRootOrigin.WORKSPACE:
        return DiskKind.WORKSPACE
    return DiskKind.DIRECTORY


def _write_result(result: ScanResult) -> None:
    target = _result_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".disk-scan.", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(result.model_dump_json())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _run(plan: ScanPlan, started_at: datetime) -> None:
    progress = ScanProgress()

    def publish(p: ScanProgress) -> None:
        with _JOB.lock:
            _JOB.status = ScanStatus(
                state=ScanState.RUNNING,
                started_at=started_at,
                entries=p.entries,
                current_root=p.current_root,
            )

    try:
        worktree_paths = frozenset(ref.path for ref in plan.worktrees.values() if not ref.is_main)
        hints = KindHints(
            worktrees=worktree_paths,
            workspaces=plan.workspace_roots,
            loregarden_data=frozenset({data_dir().resolve()}),
        )
        tree = scan_roots(
            [(root.path, _root_kind(root.origin)) for root in plan.roots],
            hints,
            progress=progress,
            cancel=_JOB.cancel,
            on_progress=publish,
        )
        for node, root in zip(tree, plan.roots, strict=False):
            node.name = root.label
        result = ScanResult(
            started_at=started_at,
            finished_at=utcnow(),
            roots=[
                RootSummary(
                    path=str(r.path),
                    label=r.label,
                    origin=r.origin,
                    workspace_slug=r.workspace_slug,
                )
                for r in plan.roots
            ],
            tree=tree,
            entries=progress.entries,
            unreadable=progress.unreadable,
            unreadable_samples=progress.unreadable_samples,
            truncated=progress.truncated,
            unreadable_workspaces=plan.unreadable_workspaces,
            missing_added=plan.missing_added,
        )
        _write_result(result)
        with _JOB.lock:
            _JOB.cached = result
            _JOB.cached_mtime = _result_path().stat().st_mtime
            _JOB.status = ScanStatus(
                state=ScanState.DONE, started_at=started_at, entries=progress.entries
            )
    except ScanCancelled:
        logger.warning("Disk scan cancelled after %d entries", progress.entries)
        with _JOB.lock:
            _JOB.status = ScanStatus(
                state=ScanState.FAILED, started_at=started_at, error="The scan was cancelled."
            )
    except Exception as exc:
        logger.exception("Disk scan failed")
        with _JOB.lock:
            _JOB.status = ScanStatus(
                state=ScanState.FAILED,
                started_at=started_at,
                entries=progress.entries,
                error=f"{type(exc).__name__}: {exc}",
            )


def start_scan(session: Session) -> ScanStatus:
    """Start a scan unless one is running; either way, answer with the live status."""
    with _JOB.lock:
        if _JOB.status.state is ScanState.RUNNING:
            return _JOB.status
    plan = scan_plan(session)
    started_at = utcnow()
    with _JOB.lock:
        if _JOB.status.state is ScanState.RUNNING:
            return _JOB.status
        _JOB.cancel.clear()
        _JOB.status = ScanStatus(state=ScanState.RUNNING, started_at=started_at)
        _JOB.thread = threading.Thread(
            target=_run, args=(plan, started_at), name="disk-scan", daemon=True
        )
        _JOB.thread.start()
        return _JOB.status


def cancel_scan() -> ScanStatus:
    _JOB.cancel.set()
    with _JOB.lock:
        return _JOB.status


def scan_status() -> ScanStatus:
    with _JOB.lock:
        return _JOB.status


def latest_result() -> ScanResult | None:
    """The last finished scan, from memory or from disk; None when there is none.

    A file that exists and does not parse raises: a corrupt result is not
    "never scanned", and answering None would show the empty state over it.
    """
    path = _result_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None
    with _JOB.lock:
        if _JOB.cached is not None and _JOB.cached_mtime == mtime:
            return _JOB.cached
    result = ScanResult.model_validate_json(path.read_text(encoding="utf-8"))
    with _JOB.lock:
        _JOB.cached = result
        _JOB.cached_mtime = mtime
    return result


def find_node(result: ScanResult, path: str) -> DiskNode | None:
    """The node at `path`, walking down from whichever root holds it."""
    target = Path(path)
    for root in result.tree:
        node: DiskNode | None = root
        root_path = Path(root.path)
        if target != root_path and not target.is_relative_to(root_path):
            continue
        while node is not None and Path(node.path) != target:
            node = next(
                (
                    child
                    for child in node.children
                    if child.kind is not DiskKind.OTHER
                    and (Path(child.path) == target or target.is_relative_to(Path(child.path)))
                ),
                None,
            )
        if node is not None:
            return node
    return None


def trimmed(node: DiskNode, depth: int, limit: int) -> DiskNode:
    """A copy of `node` cut to `depth` levels and `limit` children per level.

    Children past `limit` are not dropped silently: they fold into one `other`
    node, so the sizes still add up to the parent's.
    """
    children = node.children
    if depth <= 0:
        return node.model_copy(update={"children": []})
    kept = children[:limit]
    rest = children[limit:]
    out = [trimmed(child, depth - 1, limit) for child in kept]
    if rest:
        out.append(
            DiskNode(
                name=f"{sum(max(c.folded_count, 1) for c in rest)} more items",
                path=node.path,
                size_bytes=sum(c.size_bytes for c in rest),
                kind=DiskKind.OTHER,
                is_dir=False,
                file_count=sum(c.file_count for c in rest),
                folded_count=sum(max(c.folded_count, 1) for c in rest),
            )
        )
    return node.model_copy(update={"children": out})


class VolumeUsage(BaseModel):
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int


def volume_usage(path: Path | None = None) -> VolumeUsage:
    target = path or settings.repo_root
    usage = shutil.disk_usage(target)
    return VolumeUsage(
        path=str(target), total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free
    )


def _remove(node: DiskNode, target: Path) -> DiskNode | None:
    """Drop the node at `target` from under `node`; returns the removed node."""
    for index, child in enumerate(node.children):
        if child.kind is DiskKind.OTHER:
            continue
        child_path = Path(child.path)
        if child_path == target:
            del node.children[index]
            node.size_bytes -= child.size_bytes
            node.file_count -= child.file_count
            return child
        if target.is_relative_to(child_path):
            removed = _remove(child, target)
            if removed is not None:
                node.size_bytes -= removed.size_bytes
                node.file_count -= removed.file_count
            return removed
    return None


def remove_from_result(paths: list[str]) -> None:
    """Take paths cleanup just moved out of the stored tree, so the picture is current.

    A path the tree never held in detail (folded into `other`) leaves the
    totals as they were until the next scan; the page says when it scanned.
    """
    result = latest_result()
    if result is None:
        return
    for path in paths:
        target = Path(path)
        # A lone worktree outside its checkout is scanned as a root of its own.
        result.tree = [root for root in result.tree if Path(root.path) != target]
        result.roots = [root for root in result.roots if Path(root.path) != target]
        for root in result.tree:
            if target.is_relative_to(Path(root.path)):
                _remove(root, target)
                break
    _write_result(result)
    with _JOB.lock:
        _JOB.cached = result
        _JOB.cached_mtime = _result_path().stat().st_mtime
