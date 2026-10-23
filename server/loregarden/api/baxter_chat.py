"""Home Baxter chat API — persisted conversations, per workspace or global.

The same handlers are mounted twice. ``/workspaces/{slug}/baxter-chat`` serves
one workspace's threads; ``/baxter-chat/global`` serves the threads that answer
across every workspace, optionally focused on one initiative
(``?initiative_id=``). Each mount supplies a ``ChatOwner`` dependency, and that
is the only difference between them, so a thread from one archive is a 404
through the other.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from loregarden.db.session import get_session
from loregarden.models.domain import (
    BaxterChatSession,
    CliAdapter,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.services.baxter_chat_focus import ChatFocusError, set_focus
from loregarden.services.baxter_chat_run_service import (
    BaxterChatConflictError,
    cancel_baxter_chat_turn,
    schedule_baxter_chat_turn,
    start_baxter_chat_turn,
)
from loregarden.services.baxter_chat_scope import ChatOwner, NoGlobalChatHostError
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
from loregarden.services.chat_feature_loop import (
    FeatureLoopError,
    add_criterion,
    advance_feature_loop,
    decide_criteria,
    start_feature_loop,
)
from loregarden.services.initiative_service import load_initiative
from pydantic import BaseModel, Field
from sqlmodel import Session, select

router = APIRouter(prefix="/workspaces/{slug}/baxter-chat", tags=["baxter-chat"])
global_router = APIRouter(prefix="/baxter-chat/global", tags=["baxter-chat"])


class BaxterChatSessionCreate(BaseModel):
    title: str = ""


class BaxterChatSessionUpdate(BaseModel):
    title: str = Field(min_length=1)


class BaxterChatForkRequest(BaseModel):
    #: Cut the copied history off after this message. "" forks the whole thread,
    #: which is what the `/fork` command and the history rail still ask for.
    through_message_id: str = ""


class BaxterChatFocusUpdate(BaseModel):
    #: Ticked workspaces by slug. The thread's own workspace may be listed or not;
    #: it stays in scope either way.
    workspace_slugs: list[str] = Field(default_factory=list)
    initiative_ids: list[str] = Field(default_factory=list)


class FeatureLoopStart(BaseModel):
    #: Build an existing ticket (id or external id); the fields below are then ignored.
    existing_ticket: str = ""
    title: str = ""
    #: Where a new ticket lives; "" is the chat's host workspace.
    workspace_slug: str = ""
    #: A milestone or initiative to file the new feature under; "" makes it a milestone.
    parent_ticket_id: str = ""


class FeatureCriteriaDecision(BaseModel):
    ids: list[str] = Field(min_length=1)
    accept: bool


class FeatureCriterionCreate(BaseModel):
    text: str = Field(min_length=1)


class BaxterChatMessageCreate(BaseModel):
    content: str = ""
    #: A registered skill slug chosen from the composer's `/` menu, or "" for an
    #: ordinary message. An unknown name is rejected rather than ignored: a skill
    #: that silently does nothing is worse than one that says it isn't there.
    skill: str = ""
    #: Ids returned by the attachments endpoint for this conversation. With
    #: one, `content` may be empty; `start_baxter_chat_turn` refuses only a turn with neither.
    attachment_ids: list[str] = Field(default_factory=list)


def _chat_owner(
    request: Request,
    initiative_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> ChatOwner:
    """The archive a request addresses, read from which mount it came through.

    A ``slug`` path parameter means the workspace mount. Without one it is the
    global mount, where ``initiative_id`` picks the threads focused on that
    initiative; on a workspace route the parameter means nothing, so it is
    refused rather than ignored.
    """
    slug = request.path_params.get("slug")
    if slug is not None:
        if initiative_id is not None:
            raise HTTPException(400, "initiative_id applies to global chat only")
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
        if not workspace:
            raise HTTPException(404, "Workspace not found")
        return ChatOwner.for_workspace(workspace.id)
    if initiative_id is not None:
        try:
            load_initiative(session, initiative_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
    return ChatOwner.global_(initiative_id)


def _chat_session(session: Session, owner: ChatOwner, session_id: str) -> BaxterChatSession:
    chat_session = get_chat_session(session, owner, session_id)
    if not chat_session:
        raise HTTPException(404, "Chat session not found")
    return chat_session


def _host(session: Session, chat_session: BaxterChatSession) -> Workspace:
    """The workspace whose runtime and checkout the thread's turns use."""
    workspace = session.get(Workspace, chat_session.workspace_id)
    if not workspace:
        raise HTTPException(404, "The workspace this chat runs in no longer exists")
    return workspace


# Every endpoint is registered on both mounts; `_chat_owner` tells them apart.
_OWNED = Depends(_chat_owner)
_DB = Depends(get_session)


@router.get("/sessions")
@global_router.get("/sessions")
def list_baxter_chat_sessions(owner: ChatOwner = _OWNED, session: Session = _DB) -> list[dict]:
    return [chat_session_summary(session, row) for row in list_chat_sessions(session, owner)]


@router.post("/sessions", status_code=201)
@global_router.post("/sessions", status_code=201)
def create_baxter_chat_session(
    body: BaxterChatSessionCreate | None = None,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> dict:
    try:
        row = create_chat_session(session, owner, title=body.title if body else "")
    except NoGlobalChatHostError as exc:
        # 409: nothing is wrong with the request; the machine has no checkout yet.
        raise HTTPException(409, str(exc)) from exc
    return chat_session_snapshot(session, row)


@router.get("/sessions/{session_id}")
@global_router.get("/sessions/{session_id}")
def get_baxter_chat_session(
    session_id: str, owner: ChatOwner = _OWNED, session: Session = _DB
) -> dict:
    return chat_session_snapshot(session, _chat_session(session, owner, session_id))


@router.patch("/sessions/{session_id}")
@global_router.patch("/sessions/{session_id}")
def rename_baxter_chat_session(
    session_id: str,
    body: BaxterChatSessionUpdate,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> dict:
    chat_session = _chat_session(session, owner, session_id)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Title is required")
    # Deliberately not a `touch`: renaming a thread should not reorder the archive.
    chat_session.title = title
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)


@router.delete("/sessions/{session_id}")
@global_router.delete("/sessions/{session_id}")
def remove_baxter_chat_session(
    session_id: str, owner: ChatOwner = _OWNED, session: Session = _DB
) -> dict:
    chat_session = _chat_session(session, owner, session_id)
    try:
        delete_chat_session(session, chat_session)
    except ChatSessionHasUnpublishedWork as exc:
        # 409, not 500: the thread is deletable once the operator has dealt with
        # the work, and the message says where it is.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": session_id}


@router.post("/sessions/{session_id}/fork", status_code=201)
@global_router.post("/sessions/{session_id}/fork", status_code=201)
def fork_baxter_chat_session(
    session_id: str,
    body: BaxterChatForkRequest | None = None,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> dict:
    """Copy settled messages into a new session; leave the source alone.

    ``through_message_id`` branches from a point in the thread rather than its
    end — the per-message Fork action. An id from another conversation is a
    400, not a full copy.
    """
    chat_session = _chat_session(session, owner, session_id)
    try:
        forked = fork_chat_session(
            session,
            chat_session,
            through_message_id=(body.through_message_id if body else ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return chat_session_snapshot(session, forked)


@router.patch("/sessions/{session_id}/runtime", response_model=WorkspaceRuntimeSettings)
@global_router.patch("/sessions/{session_id}/runtime", response_model=WorkspaceRuntimeSettings)
def patch_baxter_chat_runtime(
    session_id: str,
    body: WorkspaceRuntimeUpdate,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> WorkspaceRuntimeSettings:
    chat_session = _chat_session(session, owner, session_id)
    try:
        return set_chat_runtime(session, chat_session, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/sessions/{session_id}/focus")
@global_router.put("/sessions/{session_id}/focus")
def put_baxter_chat_focus(
    session_id: str,
    body: BaxterChatFocusUpdate,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> dict:
    """Replace the workspaces and initiatives this thread works across.

    Takes effect from the next turn. An unknown or archived name is a 400 and
    nothing is written.
    """
    chat_session = _chat_session(session, owner, session_id)
    try:
        set_focus(
            session,
            chat_session,
            workspace_slugs=body.workspace_slugs,
            initiative_ids=body.initiative_ids,
        )
    except ChatFocusError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)


def _advance(session: Session, chat_session: BaxterChatSession) -> None:
    """Start the next round now rather than on the next reconcile pass.

    A round that fails to start is a 409 the composer's toast reports. The
    criteria are already marked in the round, so the Feature card shows the
    stalled round too; nothing is lost by refusing here.
    """
    try:
        advance_feature_loop(session, chat_session)
    except FeatureLoopError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/sessions/{session_id}/feature")
@global_router.post("/sessions/{session_id}/feature")
def start_baxter_chat_feature_loop(
    session_id: str,
    body: FeatureLoopStart,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> dict:
    chat_session = _chat_session(session, owner, session_id)
    try:
        start_feature_loop(
            session,
            chat_session,
            title=body.title,
            workspace_slug=body.workspace_slug,
            parent_ticket_id=body.parent_ticket_id,
            existing_ticket=body.existing_ticket,
        )
    except FeatureLoopError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)


@router.post("/sessions/{session_id}/feature/criteria")
@global_router.post("/sessions/{session_id}/feature/criteria")
def add_baxter_chat_feature_criterion(
    session_id: str,
    body: FeatureCriterionCreate,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> dict:
    """An operator-written criterion: accepted as typed, so it queues at once."""
    chat_session = _chat_session(session, owner, session_id)
    try:
        add_criterion(session, chat_session, body.text)
    except FeatureLoopError as exc:
        raise HTTPException(400, str(exc)) from exc
    _advance(session, chat_session)
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)


@router.post("/sessions/{session_id}/feature/decisions")
@global_router.post("/sessions/{session_id}/feature/decisions")
def decide_baxter_chat_feature_criteria(
    session_id: str,
    body: FeatureCriteriaDecision,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> dict:
    """Accept or drop Baxter's proposals; an accept may start the next round."""
    chat_session = _chat_session(session, owner, session_id)
    try:
        decide_criteria(session, chat_session, body.ids, accept=body.accept)
    except FeatureLoopError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.accept:
        _advance(session, chat_session)
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)


@router.post("/sessions/{session_id}/attachments", status_code=201)
@global_router.post("/sessions/{session_id}/attachments", status_code=201)
def upload_baxter_chat_attachment(
    session_id: str,
    file: UploadFile = File(...),
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> dict:
    """Store one file for a later turn in this conversation; returns its id.

    Sync on purpose, like the rest of this router: the handler touches SQLite,
    and an `async def` would run that on the event loop.
    """
    chat_session = _chat_session(session, owner, session_id)
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


@router.get("/sessions/{session_id}/attachments/{attachment_id}")
@global_router.get("/sessions/{session_id}/attachments/{attachment_id}")
def get_baxter_chat_attachment(
    session_id: str,
    attachment_id: str,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> FileResponse:
    """Serve a stored attachment back, so the thread can show what was sent."""
    chat_session = _chat_session(session, owner, session_id)
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


@router.post("/sessions/{session_id}/messages", status_code=202)
@global_router.post("/sessions/{session_id}/messages", status_code=202)
def send_baxter_chat_message(
    session_id: str,
    body: BaxterChatMessageCreate,
    owner: ChatOwner = _OWNED,
    session: Session = _DB,
) -> dict:
    """Accept the turn and run it in the background.

    The reply lands on the pending assistant row, which the client picks up by
    polling the snapshot — so a dropped connection costs the response, not the
    answer.
    """
    chat_session = _chat_session(session, owner, session_id)
    try:
        attachments = resolve_attachments(chat_session.id, body.attachment_ids)
    except ChatAttachmentError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Refused here, not in the background turn: there it would surface as a
    # failed reply after the operator had already moved on.
    if has_image(attachments):
        adapter = home_chat_adapter(_host(session, chat_session), chat_session)
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


@router.post("/sessions/{session_id}/stop")
@global_router.post("/sessions/{session_id}/stop")
def stop_baxter_chat_turn(
    session_id: str, owner: ChatOwner = _OWNED, session: Session = _DB
) -> dict:
    """Stop the in-flight turn so the composer unlocks immediately."""
    chat_session = _chat_session(session, owner, session_id)
    cancel_baxter_chat_turn(session, chat_session)
    session.refresh(chat_session)
    return chat_session_snapshot(session, chat_session)
