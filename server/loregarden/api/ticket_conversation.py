"""Talking to a ticket: its triage thread, its asides, and the runtimes they use.

Split out of `api/tickets.py`, which had reached its 1,500-line cap. This is the
cohesive piece rather than the convenient one: every endpoint here is part of one
conversation with a ticket — the triage transcript, the "by the way" asides that
deliberately do *not* disturb a running agent, and the two runtime settings those
two surfaces each own. None of them reads or writes a ticket's content, which is
what the rest of `api/tickets.py` is about.

The prefix is unchanged (`/api/tickets/...`), so no client route moves. Path
segments decide the match, so `/{ticket_id}/triage` here and `/{ticket_id}` in
`api/tickets.py` cannot shadow each other whichever router is included first.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import (
    BtwExchange,
    BtwQuestionCreate,
    Ticket,
    TriageMessageCreate,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.services import btw_service
from loregarden.services.btw_run_service import schedule_btw_exchange
from loregarden.services.cli_settings import set_ticket_orchestration_runtime
from loregarden.services.triage_run_service import (
    TriageConflictError,
    cancel_triage_turn,
    schedule_triage_turn,
    start_triage_run,
)
from loregarden.services.triage_service import set_triage_runtime, triage_snapshot
from sqlmodel import Session

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("/{ticket_id}/triage")
def get_ticket_triage(ticket_id: str, session: Session = Depends(get_session)) -> dict:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return triage_snapshot(session, ticket)


@router.post("/{ticket_id}/triage/stop")
def stop_triage_turn(ticket_id: str, session: Session = Depends(get_session)) -> dict:
    """Stop the in-flight triage turn so the composer unlocks immediately.

    Answers on an idle ticket too: the composer is locked by whatever the last
    snapshot said, so the recovery path has to be reachable from the state the
    operator is actually looking at.
    """
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    cancel_triage_turn(session, ticket_id)
    return triage_snapshot(session, ticket)


@router.post("/{ticket_id}/triage/messages", status_code=202)
def post_triage_message(
    ticket_id: str,
    body: TriageMessageCreate,
    session: Session = Depends(get_session),
) -> dict:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        user_message, run = start_triage_run(
            session, ticket, body.content, auto_approve=body.auto_approve
        )
    except TriageConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    schedule_triage_turn(run.id)
    return {
        "user_message": {
            "id": user_message.id,
            "role": user_message.role,
            "content": user_message.content,
            "created_at": user_message.created_at.isoformat(),
        },
        "run_id": run.id,
        "status": "queued",
    }


@router.get("/{ticket_id}/btw")
def get_ticket_asides(ticket_id: str, session: Session = Depends(get_session)) -> dict:
    """Asides on this ticket, newest first.

    Answered ones are also mirrored into the triage transcript; this endpoint is
    what a surface polls to watch a pending one settle, and the only place a
    failed one is visible at all.
    """
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return {
        "exchanges": [
            btw_service.exchange_view(session, exchange)
            for exchange in btw_service.list_exchanges(session, ticket_id)
        ]
    }


@router.post("/{ticket_id}/btw", status_code=202)
def post_ticket_aside(
    ticket_id: str,
    body: BtwQuestionCreate,
    session: Session = Depends(get_session),
) -> dict:
    """Ask something without disturbing whatever is running.

    Deliberately has no 409 for an active run — that conflict is the entire
    reason this endpoint exists.
    """
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        exchange = btw_service.ask(session, ticket, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    schedule_btw_exchange(exchange.id)
    return btw_service.exchange_view(session, exchange)


@router.post("/{ticket_id}/btw/{exchange_id}/escalate")
def post_aside_escalation(
    ticket_id: str,
    exchange_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Put the aside to the working agent itself, which does affect its run."""
    exchange = session.get(BtwExchange, exchange_id)
    if not exchange or exchange.ticket_id != ticket_id:
        raise HTTPException(404, "Aside not found")
    try:
        btw_service.escalate(session, exchange)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return btw_service.exchange_view(session, exchange)


@router.delete("/{ticket_id}/btw/{exchange_id}")
def delete_ticket_aside(
    ticket_id: str,
    exchange_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Dismiss an aside the operator no longer needs.

    Takes the card off the thread; the row stays, marked, because the question
    was really asked and an escalated one really reached the run. Idempotent, so
    a second click on a card that has not re-rendered yet is not a 404.
    """
    exchange = session.get(BtwExchange, exchange_id)
    if not exchange or exchange.ticket_id != ticket_id:
        raise HTTPException(404, "Aside not found")
    btw_service.delete(session, exchange)
    return {"id": exchange.id, "deleted": True}


@router.patch("/{ticket_id}/triage/runtime", response_model=WorkspaceRuntimeSettings)
def patch_triage_runtime(
    ticket_id: str,
    body: WorkspaceRuntimeUpdate,
    session: Session = Depends(get_session),
) -> WorkspaceRuntimeSettings:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        return set_triage_runtime(session, ticket, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{ticket_id}/runtime", response_model=WorkspaceRuntimeSettings)
def patch_ticket_runtime(
    ticket_id: str,
    body: WorkspaceRuntimeUpdate,
    session: Session = Depends(get_session),
) -> WorkspaceRuntimeSettings:
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        return set_ticket_orchestration_runtime(session, ticket, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
