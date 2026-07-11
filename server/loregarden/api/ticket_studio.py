from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import (
    TicketStudioClarificationsUpdate,
    TicketStudioDraftUpdate,
    TicketStudioMessageCreate,
    TicketStudioSessionCreate,
    TicketStudioSessionUpdate,
    WorkspaceRuntimeUpdate,
)
from loregarden.services.ticket_studio_service import TicketStudioService
from sqlmodel import Session

router = APIRouter(prefix="/ticket-studio", tags=["ticket-studio"])


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


@router.post("/sessions/{session_id}/messages")
def send_ticket_studio_message(
    session_id: str,
    body: TicketStudioMessageCreate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return (
            TicketStudioService(session)
            .send_message(session_id, body.content)
            .model_dump(mode="json")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/clarify")
def request_ticket_studio_clarifications(
    session_id: str, session: Session = Depends(get_session)
) -> dict:
    try:
        return (
            TicketStudioService(session).request_clarifications(session_id).model_dump(mode="json")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/clarifications")
def request_ticket_studio_clarifications_alt(
    session_id: str, session: Session = Depends(get_session)
) -> dict:
    try:
        return (
            TicketStudioService(session).request_clarifications(session_id).model_dump(mode="json")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/sessions/{session_id}/clarifications")
def save_ticket_studio_clarifications(
    session_id: str,
    body: TicketStudioClarificationsUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return (
            TicketStudioService(session)
            .save_clarifications(session_id, body.answers)
            .model_dump(mode="json")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/scope")
def generate_ticket_studio_scope(session_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        return TicketStudioService(session).generate_scope(session_id).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/commit")
def commit_ticket_studio_session(session_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        return TicketStudioService(session).commit_session(session_id).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
