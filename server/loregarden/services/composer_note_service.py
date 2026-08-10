"""Post-it notes written from the composer's `/note` command.

A note is a draft the operator wants to keep but has not decided where to send.
It belongs to the workspace rather than to a conversation: the two things a note
offers are "send this here" and "send this into a *new* chat", and the second
would be impossible if the note died with the thread it was typed beside.
"""

from __future__ import annotations

from datetime import datetime

from loregarden.models.domain import ComposerNote
from loregarden.models.domain.enums import utcnow
from sqlmodel import Session, col, select

MAX_NOTE_BYTES = 20_000


def _validated_body(body: str) -> str:
    text = (body or "").strip()
    if not text:
        raise ValueError("Note body is required")
    if len(text.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError(f"Note is too long (limit {MAX_NOTE_BYTES} bytes)")
    return text


def note_view(note: ComposerNote) -> dict:
    return {
        "id": note.id,
        "body": note.body,
        "sent_at": _isoformat(note.sent_at),
        "created_at": _isoformat(note.created_at),
        "updated_at": _isoformat(note.updated_at),
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def list_notes(session: Session, workspace_id: str) -> list[ComposerNote]:
    return list(
        session.exec(
            select(ComposerNote)
            .where(ComposerNote.workspace_id == workspace_id)
            .order_by(col(ComposerNote.updated_at).desc())
        ).all()
    )


def get_note(session: Session, workspace_id: str, note_id: str) -> ComposerNote | None:
    note = session.get(ComposerNote, note_id)
    if not note or note.workspace_id != workspace_id:
        return None
    return note


def create_note(session: Session, workspace_id: str, body: str) -> ComposerNote:
    note = ComposerNote(workspace_id=workspace_id, body=_validated_body(body))
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def update_note(
    session: Session,
    note: ComposerNote,
    *,
    body: str | None = None,
    mark_sent: bool = False,
) -> ComposerNote:
    """Edit the note's text, record that it was sent, or both.

    Sending does not delete the note: the same post-it is routinely sent into
    one conversation and then into another, so ``sent_at`` records the last send
    rather than the note's disappearance.
    """
    if body is not None:
        note.body = _validated_body(body)
    if mark_sent:
        note.sent_at = utcnow()
    note.updated_at = utcnow()
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def delete_note(session: Session, note: ComposerNote) -> None:
    session.delete(note)
    session.commit()
