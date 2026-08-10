"""Composer post-it notes — workspace-scoped drafts written by `/note`."""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import ComposerNote, Workspace
from loregarden.services.composer_note_service import (
    create_note,
    delete_note,
    get_note,
    list_notes,
    note_view,
    update_note,
)
from pydantic import BaseModel, Field
from sqlmodel import Session, select

router = APIRouter(prefix="/workspaces", tags=["composer-notes"])


class ComposerNoteCreate(BaseModel):
    body: str = Field(min_length=1)


class ComposerNoteUpdate(BaseModel):
    body: str | None = None
    #: Record that the note was sent into a conversation. The send itself is the
    #: chat endpoint's job; this only stamps the note.
    mark_sent: bool = False


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return workspace


def _note(session: Session, workspace_id: str, note_id: str) -> ComposerNote:
    note = get_note(session, workspace_id, note_id)
    if not note:
        raise HTTPException(404, "Note not found")
    return note


@router.get("/{slug}/composer-notes")
def get_composer_notes(slug: str, session: Session = Depends(get_session)) -> list[dict]:
    workspace = _workspace(session, slug)
    return [note_view(note) for note in list_notes(session, workspace.id)]


@router.post("/{slug}/composer-notes", status_code=201)
def post_composer_note(
    slug: str, body: ComposerNoteCreate, session: Session = Depends(get_session)
) -> dict:
    workspace = _workspace(session, slug)
    try:
        return note_view(create_note(session, workspace.id, body.body))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{slug}/composer-notes/{note_id}")
def patch_composer_note(
    slug: str,
    note_id: str,
    body: ComposerNoteUpdate,
    session: Session = Depends(get_session),
) -> dict:
    workspace = _workspace(session, slug)
    note = _note(session, workspace.id, note_id)
    try:
        return note_view(update_note(session, note, body=body.body, mark_sent=body.mark_sent))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{slug}/composer-notes/{note_id}")
def remove_composer_note(slug: str, note_id: str, session: Session = Depends(get_session)) -> dict:
    workspace = _workspace(session, slug)
    delete_note(session, _note(session, workspace.id, note_id))
    return {"deleted": note_id}
