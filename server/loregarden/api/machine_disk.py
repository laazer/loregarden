"""Machine → Disk: what is using the disk, and a careful way to clean some of it.

Reads (volume, scan status, the size tree), the scan itself (metadata only),
the folders a person added to it, and the two-step cleanup: preview, then
execute what the person confirmed. Cleanup is a person's action. The MCP tool
built on this module's services reads sizes and never reaches these writes.

Every handler is `def`, not `async def`: they stat, walk and shell out to git,
and a blocking call in an async handler stalls the event loop for everyone.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.db.session import get_session
from loregarden.models.domain import DiskScanRoot
from loregarden.services.disk_usage.cleanup import (
    MAX_PATHS,
    CleanupPlan,
    CleanupResult,
    PlanNotFound,
    execute,
    preview,
    recent_events,
)
from loregarden.services.disk_usage.roots import added_roots
from loregarden.services.disk_usage.scanner import DiskNode
from loregarden.services.disk_usage.store import (
    RootSummary,
    ScanStatus,
    VolumeUsage,
    find_node,
    latest_result,
    scan_status,
    start_scan,
    trimmed,
    volume_usage,
)
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, select

router = APIRouter(prefix="/machine/disk", tags=["machine-disk"])

MAX_DEPTH = 4
MAX_LIMIT = 200


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


class AddRootRequest(BaseModel):
    path: str = Field(min_length=1)


class PreviewRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=MAX_PATHS)


class ExecuteRequest(BaseModel):
    plan_id: str = Field(min_length=1)
    paths: list[str] = Field(min_length=1, max_length=MAX_PATHS)


class CleanupEventOut(BaseModel):
    path: str
    size_bytes: int
    kind: str
    method: str
    outcome: str
    detail: str
    created_at: datetime


def _latest_or_500():
    try:
        return latest_result()
    except (OSError, ValidationError) as exc:
        raise HTTPException(
            500, f"The stored disk scan could not be read: {exc}. Scan again."
        ) from exc


@router.get("")
def disk_overview(session: Session = Depends(get_session)) -> DiskOverview:
    result = _latest_or_500()
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


@router.post("/scan", status_code=202)
def disk_scan(session: Session = Depends(get_session)) -> ScanStatus:
    return start_scan(session)


@router.get("/tree")
def disk_tree(
    path: str = Query("", description="A node's path; empty for every root."),
    depth: int = Query(2, ge=1, le=MAX_DEPTH),
    limit: int = Query(80, ge=1, le=MAX_LIMIT),
) -> list[DiskNode]:
    result = _latest_or_500()
    if result is None:
        raise HTTPException(404, "There is no disk scan yet.")
    if not path:
        return [trimmed(root, depth, limit) for root in result.tree]
    node = find_node(result, path)
    if node is None:
        raise HTTPException(404, f"The last scan has no detail for {path}.")
    return [trimmed(node, depth, limit)]


def _validated_root(raw: str) -> Path:
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise HTTPException(400, "Give an absolute folder path, like ~/Library/Caches.")
    path = path.resolve()
    if path == Path("/"):
        raise HTTPException(400, "Scanning the whole disk is not supported. Pick a folder.")
    if not path.is_dir():
        raise HTTPException(400, f"{path} is not a folder.")
    return path


@router.post("/roots", status_code=201)
def add_root(body: AddRootRequest, session: Session = Depends(get_session)) -> list[str]:
    path = _validated_root(body.path)
    existing = session.exec(select(DiskScanRoot).where(DiskScanRoot.path == str(path))).first()
    if existing is None:
        session.add(DiskScanRoot(path=str(path)))
        session.commit()
    return [root.path for root in added_roots(session)]


@router.delete("/roots")
def remove_root(
    path: str = Query(..., min_length=1), session: Session = Depends(get_session)
) -> list[str]:
    row = session.exec(select(DiskScanRoot).where(DiskScanRoot.path == path)).first()
    if row is None:
        raise HTTPException(404, f"{path} is not an added folder.")
    session.delete(row)
    session.commit()
    return [root.path for root in added_roots(session)]


@router.post("/cleanup/preview")
def cleanup_preview(body: PreviewRequest, session: Session = Depends(get_session)) -> CleanupPlan:
    try:
        return preview(session, body.paths)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/cleanup/execute")
def cleanup_execute(body: ExecuteRequest, session: Session = Depends(get_session)) -> CleanupResult:
    try:
        return execute(session, body.plan_id, body.paths)
    except PlanNotFound as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/cleanup/history")
def cleanup_history(
    limit: int = Query(20, ge=1, le=200), session: Session = Depends(get_session)
) -> list[CleanupEventOut]:
    return [
        CleanupEventOut.model_validate(event, from_attributes=True)
        for event in recent_events(session, limit)
    ]
