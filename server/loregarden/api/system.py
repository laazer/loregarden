from fastapi import APIRouter, HTTPException

from loregarden.config import settings
from loregarden.services.path_browser import list_browse, list_import_browse, resolve_browse_target

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/browse")
def browse_directory(path: str | None = None) -> dict:
    try:
        target = resolve_browse_target(path, repo_root=settings.repo_root)
        return list_browse(target, repo_root=settings.repo_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/browse-import")
def browse_import_directory(path: str | None = None) -> dict:
    try:
        target = resolve_browse_target(path, repo_root=settings.repo_root)
        return list_import_browse(target, repo_root=settings.repo_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
