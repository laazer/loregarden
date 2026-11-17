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
from loregarden.api.operation_routes import run_rest_operation
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
from loregarden.services.disk_usage.docker_reclaim import (
    DockerOutputUnreadable,
    DockerReclaimPreview,
    DockerReclaimRequest,
    DockerReclaimResult,
    DockerUnavailable,
)
from loregarden.services.disk_usage.docker_reclaim import preview as docker_preview
from loregarden.services.disk_usage.docker_reclaim import reclaim as docker_reclaim
from loregarden.services.disk_usage.local_models import (
    LocalModelList,
    ModelDeleteResult,
    ModelNotFound,
    delete_model,
    list_models,
)
from loregarden.services.disk_usage.roots import added_roots
from loregarden.services.disk_usage.scanner import DiskNode
from loregarden.services.disk_usage.store import ScanStatus
from loregarden.services.operations import OperationId
from loregarden.services.operations.disk_operations import MAX_DEPTH, MAX_LIMIT, DiskOverview
from pydantic import BaseModel, Field
from sqlmodel import Session, select

router = APIRouter(prefix="/machine/disk", tags=["machine-disk"])


class AddRootRequest(BaseModel):
    path: str = Field(min_length=1)


class PreviewRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=MAX_PATHS)


class ExecuteRequest(BaseModel):
    plan_id: str = Field(min_length=1)
    paths: list[str] = Field(min_length=1, max_length=MAX_PATHS)


class DeleteModelRequest(BaseModel):
    id: str = Field(min_length=1)


class CleanupEventOut(BaseModel):
    path: str
    size_bytes: int
    kind: str
    method: str
    outcome: str
    detail: str
    created_at: datetime


@router.get("")
def disk_overview(session: Session = Depends(get_session)) -> DiskOverview:
    return run_rest_operation(session, OperationId.DISK_OVERVIEW, {})


@router.post("/scan", status_code=202)
def disk_scan(session: Session = Depends(get_session)) -> ScanStatus:
    return run_rest_operation(session, OperationId.DISK_SCAN, {})


@router.get("/tree")
def disk_tree(
    path: str = Query("", description="A node's path; empty for every root."),
    depth: int = Query(2, ge=1, le=MAX_DEPTH),
    limit: int = Query(80, ge=1, le=MAX_LIMIT),
    session: Session = Depends(get_session),
) -> list[DiskNode]:
    return run_rest_operation(
        session, OperationId.DISK_TREE, {"path": path, "depth": depth, "limit": limit}
    )


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


@router.get("/docker")
def docker_reclaimable() -> DockerReclaimPreview:
    """What Docker holds that nothing uses. 503 when Docker cannot be asked."""
    try:
        return docker_preview()
    except DockerUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except DockerOutputUnreadable as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/docker/reclaim")
def docker_reclaim_confirmed(
    body: DockerReclaimRequest, session: Session = Depends(get_session)
) -> DockerReclaimResult:
    try:
        return docker_reclaim(session, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except DockerUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except DockerOutputUnreadable as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/models")
def local_models() -> LocalModelList:
    return list_models()


@router.post("/models/delete")
def delete_local_model(
    body: DeleteModelRequest, session: Session = Depends(get_session)
) -> ModelDeleteResult:
    try:
        return delete_model(session, body.id)
    except ModelNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
