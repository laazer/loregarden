from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.config import settings
from loregarden.db.session import get_session
from loregarden.services.path_browser import (
    list_browse,
    list_import_browse,
    normalize_browse_target,
)
from loregarden.services.self_improve_restart import evaluate_self_improve_restart
from sqlmodel import Session

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/browse")
def browse_directory(path: str | None = None) -> dict:
    try:
        target = normalize_browse_target(path, repo_root=settings.repo_root)
        return list_browse(target, repo_root=settings.repo_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/browse-import")
def browse_import_directory(path: str | None = None) -> dict:
    try:
        target = normalize_browse_target(path, repo_root=settings.repo_root)
        return list_import_browse(target, repo_root=settings.repo_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/self-improve-restart")
def self_improve_restart_status(
    workspace: str = Query(default="loregarden"),
    session: Session = Depends(get_session),
) -> dict:
    """Report whether the dev server can restart after a human-triage handoff."""
    try:
        return evaluate_self_improve_restart(session, workspace_slug=workspace)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
