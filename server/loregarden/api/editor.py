from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import Workspace
from loregarden.services.file_editor import (
    checkout_editor_branch,
    list_editor_browse,
    list_editor_refs,
    read_editor_file,
    write_editor_file,
)
from pydantic import BaseModel
from sqlmodel import Session, select

router = APIRouter(prefix="/workspaces", tags=["editor"])


def _workspace_or_404(session: Session, slug: str) -> Workspace:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return ws


class EditorCheckoutRequest(BaseModel):
    branch: str | None = None
    worktree_path: str | None = None


class EditorWriteRequest(BaseModel):
    path: str
    content: str
    context_root: str | None = None


@router.get("/{slug}/editor/refs")
def editor_refs(
    slug: str, context_root: str | None = None, session: Session = Depends(get_session)
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        return list_editor_refs(ws, context_root=context_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{slug}/editor/checkout")
def editor_checkout(
    slug: str,
    body: EditorCheckoutRequest,
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        if body.worktree_path:
            return list_editor_refs(ws, context_root=body.worktree_path)
        if body.branch:
            return checkout_editor_branch(ws, body.branch)
        raise ValueError("Provide branch or worktree_path")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{slug}/editor/browse")
def editor_browse(
    slug: str,
    path: str | None = None,
    context_root: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        return list_editor_browse(ws, path, context_root=context_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{slug}/editor/file")
def editor_read_file(
    slug: str,
    path: str,
    context_root: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        return read_editor_file(ws, path, context_root=context_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/{slug}/editor/file")
def editor_write_file(
    slug: str,
    body: EditorWriteRequest,
    session: Session = Depends(get_session),
) -> dict:
    ws = _workspace_or_404(session, slug)
    try:
        return write_editor_file(ws, body.path, body.content, context_root=body.context_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
