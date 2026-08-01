from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import ReferenceRepoCreate
from loregarden.services.reference_repo_service import ReferenceRepoError, ReferenceRepoService
from sqlmodel import Session

router = APIRouter(prefix="/reference-repos", tags=["reference-repos"])


@router.get("")
def list_reference_repos(
    workspace: str,
    session: Session = Depends(get_session),
) -> list[dict]:
    try:
        return [
            item.model_dump(mode="json")
            for item in ReferenceRepoService(session).list_repos(workspace_slug=workspace)
        ]
    except ReferenceRepoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("")
def add_reference_repo(
    body: ReferenceRepoCreate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return ReferenceRepoService(session).add_repo(body).model_dump(mode="json")
    except ReferenceRepoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{repo_id}/sync")
def sync_reference_repo(repo_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        return ReferenceRepoService(session).sync_repo(repo_id).model_dump(mode="json")
    except ReferenceRepoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{repo_id}")
def delete_reference_repo(
    repo_id: str,
    remove_clone: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    try:
        ReferenceRepoService(session).delete_repo(repo_id, remove_clone=remove_clone)
    except ReferenceRepoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}
