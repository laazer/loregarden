"""Home Baxter chat API — persisted, workspace-scoped conversations."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from loregarden.db.session import get_session
from loregarden.models.domain import (
    BaxterChatSession,
    CliAdapter,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.services.baxter_chat_run_service import (
    BaxterChatConflictError,
    cancel_baxter_chat_turn,
    schedule_baxter_chat_turn,
    start_baxter_chat_turn,
)
from loregarden.services.baxter_chat_service import (
    ChatSessionHasUnpublishedWork,
    chat_session_snapshot,
    chat_session_summary,
    create_chat_session,
    delete_chat_session,
    fork_chat_session,
    get_chat_session,
    home_chat_adapter,
    list_chat_sessions,
    set_chat_runtime,
)
from loregarden.services.chat_attachments import (
    MAX_IMAGE_BYTES,
    ChatAttachmentError,
    attachment_path,
    has_image,
    image_adapter_refusal,
    resolve_attachments,
    save_attachment,
)
from pydantic import BaseModel, Field
from sqlmodel import Session, select

router = APIRouter(prefix="/workspaces", tags=["baxter-chat"])


class BaxterChatSessionCreate(BaseModel):
    title: str = ""


class BaxterChatSessionUpdate(BaseModel):
    title: str = Field(min_length=1)


class BaxterChatForkRequest(BaseModel):
    #: Cut the copied history off after this message. "" forks the whole thread,
    #: which is what the `/fork` command and the history rail still ask for.
    through_message_id: str = ""


class BaxterChatMessageCreate(BaseModel):
    content: str = ""
    #: A registered skill slug chosen from the composer's `/` menu, or "" for an
    #: ordinary message. An unknown name is rejected rather than ignored: a skill
    #: that silently does nothing is worse than one that says it isn't there.
    skill: str = ""
    #: Ids returned by the attachments endpoint for this conversation. With
    #: one, `content` may be empty; `start_baxter_chat_turn` refuses only a turn with neither.
    attachment_ids: list[str] = Field(default_factory=list)


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
    try:
        delete_chat_session(session, chat_session)
    except ChatSessionHasUnpublishedWork as exc:
        # 409, not 500: the thread is deletable once the operator has dealt with
        # the work, and the message says where it is.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": session_id}


@router.post("/{slug}/baxter-chat/sessions/{session_id}/fork", status_code=201)
def fork_baxter_chat_session(
    slug: str,
    session_id: str,
    body: BaxterChatForkRequest | None = None,
    session: Session = Depends(get_session),
) -> dict:
    """Copy settled messages into a new session; leave the source alone.

    ``through_message_id`` branches from a point in the thread rather than its
    end — the per-message Fork action. An id from another conversation is a
    400, not a full copy.
    """
    workspace = _workspace(session, slug)
    chat_session = _chat_session(session, workspace.id, session_id)
    try:
        forked = fork_chat_session(
            session,
            chat_session,
            through_message_id=(body.through_message_id if body else ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return chat_session_snapshot(session, forked)


@router.patch(
    "/{slug}/baxter-chat/sessions/{session_id}/runtime",
    response_model=WorkspaceRuntimeSettings,
)
def patch_baxter_chat_runtime(
    slug: str,
    session_id: str,
    body: WorkspaceRuntimeUpdate,
    session: Session = Depends(get_session),
) -> WorkspaceRuntimeSettings:
    workspace = _workspace(session, slug)
    chat_session = _chat_session(session, workspace.id, session_id)
    try:
        return set_chat_runtime(session, chat_session, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{slug}/baxter-chat/sessions/{session_id}/attachments", status_code=201)
def upload_baxter_chat_attachment(
    slug: str,
    session_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict:
    """Store one file for a later turn in this conversation; returns its id.

    Sync on purpose, like the rest of this router: the handler touches SQLite,
    and an `async def` would run that on the event loop.
    """
    workspace = _workspace(session, slug)
    chat_session = _chat_session(session, workspace.id, session_id)
    # One byte over the largest limit is enough to refuse; never buffer more.
    data = file.file.read(MAX_IMAGE_BYTES + 1)
    try:
        attachment = save_attachment(
            chat_session.id,
            name=file.filename or "attachment",
            mime=file.content_type or "",
            data=data,
        )
    except ChatAttachmentError as exc:
        raise HTTPException(400, str(exc)) from exc
    return attachment.model_dump(mode="json")


@router.get("/{slug}/baxter-chat/sessions/{session_id}/attachments/{attachment_id}")
def get_baxter_chat_attachment(
    slug: str,
    session_id: str,
    attachment_id: str,
    session: Session = Depends(get_session),
) -> FileResponse:
    """Serve a stored attachment back, so the thread can show what was sent."""
    workspace = _workspace(session, slug)
    chat_session = _chat_session(session, workspace.id, session_id)
    try:
        [attachment] = resolve_attachments(chat_session.id, [attachment_id])
    except ChatAttachmentError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(
        attachment_path(chat_session.id, attachment),
        media_type=attachment.mime,
        filename=attachment.name,
        # Inline for the thumbnail; an uploaded .html or .svg must not run as a page here.
        content_disposition_type="inline",
        headers={
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
        attachments = resolve_attachments(chat_session.id, body.attachment_ids)
    except ChatAttachmentError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Refused here, not in the background turn: there it would surface as a
    # failed reply after the operator had already moved on.
    if has_image(attachments):
        adapter = home_chat_adapter(workspace, chat_session)
        if adapter != CliAdapter.CLAUDE:
            raise HTTPException(400, image_adapter_refusal(adapter))
    try:
        _user_message, assistant_message = start_baxter_chat_turn(
            session,
            chat_session,
            body.content,
            skill_name=body.skill,
            attachments=attachments,
        )
    except BaxterChatConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    schedule_baxter_chat_turn(assistant_message.id)
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)


@router.post("/{slug}/baxter-chat/sessions/{session_id}/stop")
def stop_baxter_chat_turn(
    slug: str,
    session_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Stop the in-flight turn so the composer unlocks immediately."""
    workspace = _workspace(session, slug)
    chat_session = _chat_session(session, workspace.id, session_id)
    cancel_baxter_chat_turn(session, chat_session)
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)
