from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import (
    TicketStudioClarificationsUpdate,
    TicketStudioDraftUpdate,
    TicketStudioMessageCreate,
    TicketStudioReferenceReposUpdate,
    TicketStudioSessionCreate,
    TicketStudioSessionUpdate,
    TicketStudioSessionView,
    TicketStudioSurveyUpdate,
    WorkspaceRuntimeUpdate,
)
from loregarden.services.ticket_studio_run_service import schedule_studio_turn
from loregarden.services.ticket_studio_service import (
    TicketStudioConflictError,
    TicketStudioService,
)
from pydantic import BaseModel
from sqlmodel import Session

router = APIRouter(prefix="/ticket-studio", tags=["ticket-studio"])


class TicketStudioClarifyRequest(BaseModel):
    """``auto_scope`` bootstraps a new session: generate the breakdown straight
    away when the scoper has nothing to ask."""

    auto_scope: bool = False


def _accept_turn(session: Session, view: TicketStudioSessionView) -> dict:
    """Queue the turn the caller just started and return the session as it stands.

    The reply lands on the pending row and reaches the panel by polling, so a
    dropped connection costs the response, not the scoping work.

    The session is re-read after scheduling rather than returned as captured:
    normally that is the same running view, but it is the settled one wherever
    turns run inline (``LOREGARDEN_SYNC_RUNS``), and a caller should never be
    handed a view the server has already moved past.
    """
    if not view.active_turn_id:
        return view.model_dump(mode="json")
    schedule_studio_turn(view.active_turn_id)
    # The turn commits on its own session, so this one must drop what it cached.
    session.expire_all()
    current = TicketStudioService(session).get_session(view.id)
    return (current or view).model_dump(mode="json")


@router.get("/sessions")
def list_ticket_studio_sessions(
    workspace: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict]:
    return [
        item.model_dump(mode="json")
        for item in TicketStudioService(session).list_sessions(workspace_slug=workspace)
    ]


@router.post("/sessions")
def create_ticket_studio_session(
    body: TicketStudioSessionCreate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return TicketStudioService(session).create_session(body).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions/{session_id}")
def get_ticket_studio_session(session_id: str, session: Session = Depends(get_session)) -> dict:
    row = TicketStudioService(session).get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return row.model_dump(mode="json")


@router.patch("/sessions/{session_id}")
def update_ticket_studio_session(
    session_id: str,
    body: TicketStudioSessionUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return TicketStudioService(session).update_session(session_id, body).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/sessions/{session_id}")
def delete_ticket_studio_session(session_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        TicketStudioService(session).delete_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.patch("/sessions/{session_id}/runtime")
def set_ticket_studio_runtime(
    session_id: str,
    body: WorkspaceRuntimeUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return TicketStudioService(session).set_runtime(session_id, body).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/sessions/{session_id}/draft")
def update_ticket_studio_draft(
    session_id: str,
    body: TicketStudioDraftUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return (
            TicketStudioService(session)
            .update_draft(session_id, body.items)
            .model_dump(mode="json")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/messages", status_code=202)
def send_ticket_studio_message(
    session_id: str,
    body: TicketStudioMessageCreate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return _accept_turn(
            session, TicketStudioService(session).send_message(session_id, body.content)
        )
    except TicketStudioConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/clarify", status_code=202)
def request_ticket_studio_clarifications(
    session_id: str,
    body: TicketStudioClarifyRequest | None = None,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return _accept_turn(
            session,
            TicketStudioService(session).request_clarifications(
                session_id, auto_scope=bool(body and body.auto_scope)
            ),
        )
    except TicketStudioConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/clarifications", status_code=202)
def request_ticket_studio_clarifications_alt(
    session_id: str,
    body: TicketStudioClarifyRequest | None = None,
    session: Session = Depends(get_session),
) -> dict:
    return request_ticket_studio_clarifications(session_id, body, session)


@router.patch("/sessions/{session_id}/clarifications", status_code=202)
def save_ticket_studio_clarifications(
    session_id: str,
    body: TicketStudioClarificationsUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return _accept_turn(
            session, TicketStudioService(session).save_clarifications(session_id, body.answers)
        )
    except TicketStudioConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/scope", status_code=202)
def generate_ticket_studio_scope(session_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        return _accept_turn(session, TicketStudioService(session).generate_scope(session_id))
    except TicketStudioConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/sessions/{session_id}/reference-repos")
def set_ticket_studio_reference_repos(
    session_id: str,
    body: TicketStudioReferenceReposUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return (
            TicketStudioService(session)
            .set_reference_repos(session_id, body.reference_repo_ids)
            .model_dump(mode="json")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/survey")
def generate_ticket_studio_survey(session_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        return TicketStudioService(session).generate_survey(session_id).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/sessions/{session_id}/survey")
def save_ticket_studio_survey(
    session_id: str,
    body: TicketStudioSurveyUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return (
            TicketStudioService(session)
            .save_survey(session_id, body.findings)
            .model_dump(mode="json")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/commit")
def commit_ticket_studio_session(session_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        return TicketStudioService(session).commit_session(session_id).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
