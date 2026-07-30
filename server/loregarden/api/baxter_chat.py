"""Home Baxter chat API — persisted, workspace-scoped conversations."""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import BaxterChatSession, Workspace
from loregarden.services.baxter_chat_run_service import (
    BaxterChatConflictError,
    schedule_baxter_chat_turn,
    start_baxter_chat_turn,
)
from loregarden.services.baxter_chat_service import (
    chat_session_snapshot,
    chat_session_summary,
    create_chat_session,
    delete_chat_session,
    get_chat_session,
    list_chat_sessions,
)
from pydantic import BaseModel, Field
from sqlmodel import Session, select

router = APIRouter(prefix="/workspaces", tags=["baxter-chat"])


class BaxterChatSessionCreate(BaseModel):
    title: str = ""


class BaxterChatSessionUpdate(BaseModel):
    title: str = Field(min_length=1)


class BaxterChatMessageCreate(BaseModel):
    content: str = Field(min_length=1)


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return workspace


def _chat_session(session: Session, workspace_id: str, session_id: str) -> BaxterChatSession:
    chat_session = get_chat_session(session, workspace_id, session_id)
    if not chat_session:
        raise HTTPException(404, "Chat session not found")
    return chat_session


@router.get("/{slug}/baxter-chat/sessions")
def list_baxter_chat_sessions(slug: str, session: Session = Depends(get_session)) -> list[dict]:
    workspace = _workspace(session, slug)
    return [chat_session_summary(session, row) for row in list_chat_sessions(session, workspace.id)]


@router.post("/{slug}/baxter-chat/sessions", status_code=201)
def create_baxter_chat_session(
    slug: str,
    body: BaxterChatSessionCreate | None = None,
    session: Session = Depends(get_session),
) -> dict:
    workspace = _workspace(session, slug)
    row = create_chat_session(session, workspace.id, title=body.title if body else "")
    return chat_session_snapshot(session, row)


@router.get("/{slug}/baxter-chat/sessions/{session_id}")
def get_baxter_chat_session(
    slug: str, session_id: str, session: Session = Depends(get_session)
) -> dict:
    workspace = _workspace(session, slug)
    return chat_session_snapshot(session, _chat_session(session, workspace.id, session_id))


@router.patch("/{slug}/baxter-chat/sessions/{session_id}")
def rename_baxter_chat_session(
    slug: str,
    session_id: str,
    body: BaxterChatSessionUpdate,
    session: Session = Depends(get_session),
) -> dict:
    workspace = _workspace(session, slug)
    chat_session = _chat_session(session, workspace.id, session_id)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Title is required")
    # Deliberately not a `touch`: renaming a thread should not reorder the archive.
    chat_session.title = title
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)


@router.delete("/{slug}/baxter-chat/sessions/{session_id}")
def remove_baxter_chat_session(
    slug: str, session_id: str, session: Session = Depends(get_session)
) -> dict:
    workspace = _workspace(session, slug)
    chat_session = _chat_session(session, workspace.id, session_id)
    delete_chat_session(session, chat_session)
    return {"deleted": session_id}


@router.post("/{slug}/baxter-chat/sessions/{session_id}/messages", status_code=202)
def send_baxter_chat_message(
    slug: str,
    session_id: str,
    body: BaxterChatMessageCreate,
    session: Session = Depends(get_session),
) -> dict:
    """Accept the turn and run it in the background.

    The reply lands on the pending assistant row, which the client picks up by
    polling the snapshot — so a dropped connection costs the response, not the
    answer.
    """
    workspace = _workspace(session, slug)
    chat_session = _chat_session(session, workspace.id, session_id)
    try:
        _user_message, assistant_message = start_baxter_chat_turn(
            session, chat_session, body.content
        )
    except BaxterChatConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    schedule_baxter_chat_turn(assistant_message.id)
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)
