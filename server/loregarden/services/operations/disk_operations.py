"""Where the disk went, as operations: the agent's answer, and the page's three
calls (overview, scan, size tree).

An agent and the page ask different questions of the same scan. The page shows
the overview, starts a scan, and walks the tree at its own size (four levels,
up to 200 children, raw nodes). An agent gets one smaller answer (three levels,
up to 30 children, sizes worded and cleanup candidates marked) and can start a
scan in the same call. Each behaviour has one function here: reading the last
scan, cutting a tree out of it, and starting a scan, which `disk_usage` and
`disk_scan` both call.

Starting a scan writes nothing to the database (it starts a background walk
that stores its result as a file), but it is a side effect, so `disk_usage`,
which can start one, is declared a write. The reads are only reads.

Cleanup is a person's action on the page, behind a preview and a
confirmation; none of these reach it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loregarden.models.domain import REGENERABLE_KINDS, DiskKind
from loregarden.services.disk_usage.roots import added_roots
from loregarden.services.disk_usage.scanner import DiskNode
from loregarden.services.disk_usage.store import (
    RootSummary,
    ScanResult,
    ScanStatus,
    VolumeUsage,
    find_node,
    latest_result,
    scan_status,
    start_scan,
    trimmed,
    volume_usage,
)
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

#: The page's tree limits.
MAX_DEPTH = 4
MAX_LIMIT = 200


class NoDiskScan(LookupError):  # noqa: N818 — reads as the condition, like StopIteration
    """No scan has finished on this machine yet."""


class DiskNodeNotFound(LookupError):  # noqa: N818 — reads as the condition
    """The last scan has no detail for the path asked about."""


class DiskScanUnreadable(RuntimeError):  # noqa: N818 — reads as the condition
    """The stored scan exists and could not be read: not "never scanned"."""


class ScanSummary(BaseModel):
    started_at: datetime
    finished_at: datetime
    entries: int
    unreadable: int
    unreadable_samples: list[str]
    truncated: bool
    unreadable_workspaces: list[str]
    missing_added: list[str]
    total_bytes: int
    roots: list[RootSummary]


class DiskOverview(BaseModel):
    volume: VolumeUsage
    status: ScanStatus
    #: The last finished scan, or None when there has never been one.
    scan: ScanSummary | None
    added_roots: list[str]


class DiskUsageRequest(BaseModel):
    """An agent's question; unknown keys are refused, as the tool's schema says."""

    model_config = ConfigDict(extra="forbid")

    path: str = ""
    depth: int = Field(default=2, ge=1, le=3)
    limit: int = Field(default=12, ge=1, le=30)
    rescan: bool = False


class DiskTreeQuery(BaseModel):
    path: str = ""
    depth: int = Field(default=2, ge=1, le=MAX_DEPTH)
    limit: int = Field(default=80, ge=1, le=MAX_LIMIT)


class NoArgs(BaseModel):
    pass


def _latest() -> ScanResult | None:
    try:
        return latest_result()
    except (OSError, ValidationError) as exc:
        raise DiskScanUnreadable(
            f"The stored disk scan could not be read: {exc}. Scan again."
        ) from exc


def _tree(result: ScanResult, path: str, depth: int, limit: int) -> list[DiskNode]:
    """Every root, or the node at `path`, cut to `depth` and `limit`."""
    if not path:
        return [trimmed(root, depth, limit) for root in result.tree]
    node = find_node(result, path)
    if node is None:
        raise DiskNodeNotFound(
            f"The last scan has no detail for {path}; pick a path from an earlier answer."
        )
    return [trimmed(node, depth, limit)]


def _scan(session: Session, _args: NoArgs, _caller: Caller) -> ScanStatus:
    """Start a scan unless one is running; either way, the live status."""
    return start_scan(session)


def _overview(session: Session, _args: NoArgs, _caller: Caller) -> DiskOverview:
    result = _latest()
    summary = None
    if result is not None:
        summary = ScanSummary(
            started_at=result.started_at,
            finished_at=result.finished_at,
            entries=result.entries,
            unreadable=result.unreadable,
            unreadable_samples=result.unreadable_samples,
            truncated=result.truncated,
            unreadable_workspaces=result.unreadable_workspaces,
            missing_added=result.missing_added,
            total_bytes=sum(node.size_bytes for node in result.tree),
            roots=result.roots,
        )
    return DiskOverview(
        volume=volume_usage(),
        status=scan_status(),
        scan=summary,
        added_roots=[root.path for root in added_roots(session)],
    )


def _page_tree(_session: Session, query: DiskTreeQuery, _caller: Caller) -> list[DiskNode]:
    result = _latest()
    if result is None:
        raise NoDiskScan("There is no disk scan yet.")
    return _tree(result, query.path, query.depth, query.limit)


def _size(size: int) -> str:
    for unit, scale in (("TB", 1 << 40), ("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} B"


def _shape(node: DiskNode) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": node.name,
        "path": node.path,
        "size": _size(node.size_bytes),
        "size_bytes": node.size_bytes,
        "kind": node.kind.value,
        "files": node.file_count,
    }
    if node.kind in REGENERABLE_KINDS or node.kind is DiskKind.WORKTREE:
        out["cleanup"] = "candidate: a person can review it on Machine → Disk"
    if node.children:
        out["children"] = [_shape(child) for child in node.children]
    return out


def _agent_answer(session: Session, request: DiskUsageRequest, caller: Caller) -> dict[str, Any]:
    """Names, sizes and kinds from the last scan, largest first; metadata only."""
    status = _scan(session, NoArgs(), caller) if request.rescan else scan_status()
    volume = volume_usage()
    answer: dict[str, Any] = {
        "volume": {
            "free": _size(volume.free_bytes),
            "used": _size(volume.used_bytes),
            "total": _size(volume.total_bytes),
        },
        "scan_status": status.model_dump(mode="json"),
    }
    result = _latest()
    if result is None:
        # Not an error for an agent: the answer says how to get a scan.
        answer["note"] = "No finished scan yet. Call again with rescan=true, then again to read it."
        return answer
    answer["scanned_at"] = result.finished_at.isoformat()
    if result.unreadable:
        answer["unreadable_folders"] = result.unreadable
    if result.truncated:
        answer["truncated"] = True
    answer["tree"] = [
        _shape(node) for node in _tree(result, request.path, request.depth, request.limit)
    ]
    return answer


_AGENT_ONLY = {
    Transport.REST: "the agent's one-call answer; the page reads disk_overview and "
    "disk_tree, and starts a scan with disk_scan",
}
_PAGE_ONLY_READ = {
    Transport.MCP: "the page's raw view; agents read loregarden_disk_usage, which "
    "carries the volume, the scan status and a smaller, worded tree",
    Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
}

DISK_OPERATIONS = (
    Operation(
        id=OperationId.DISK_USAGE,
        effect=Effect.WRITE,
        args=DiskUsageRequest,
        handler=_agent_answer,
        unavailable_on=_AGENT_ONLY,
    ),
    Operation(
        id=OperationId.DISK_OVERVIEW,
        effect=Effect.READ,
        args=NoArgs,
        handler=_overview,
        unavailable_on=_PAGE_ONLY_READ,
    ),
    Operation(
        id=OperationId.DISK_SCAN,
        effect=Effect.WRITE,
        args=NoArgs,
        handler=_scan,
        unavailable_on={
            Transport.MCP: "agents start a scan with loregarden_disk_usage rescan=true",
            Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
        },
    ),
    Operation(
        id=OperationId.DISK_TREE,
        effect=Effect.READ,
        args=DiskTreeQuery,
        handler=_page_tree,
        unavailable_on=_PAGE_ONLY_READ,
    ),
)
