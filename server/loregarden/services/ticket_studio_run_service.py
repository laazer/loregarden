"""Background execution for Ticket Studio scoper turns.

The fourth chat surface to get the lifecycle ``branch_triage_run_service.py``
established, and the one that needed it most: a scope turn returns a whole
ticket hierarchy, so it is both the slowest and the most expensive to lose. The
turn's state lives on the assistant ``TicketStudioMessage`` row (``status``,
``turn_mode``) rather than on the request, so a restart settles it instead of
silently dropping work the operator watched start.

A scoper turn is not only a message: the reply also updates the session's
summary, its open questions, and its draft. That half runs here too, through
``apply_settled_turn``, keyed by the mode recorded on the row.
"""

from __future__ import annotations

import logging
import os
import threading

from loregarden.db.session import engine
from loregarden.models.domain import TicketStudioMessage, TicketStudioSession
from loregarden.models.domain.enums import utcnow
from loregarden.services.chat_primitives import EMPTY_PARTS_JSON, parts_json_for_reply
from loregarden.services.ticket_studio_service import (
    STUDIO_PROMPT_MODES,
    STUDIO_TURN_BOOTSTRAP_CLARIFY,
    STUDIO_TURN_CHAT,
    STUDIO_TURN_SCOPE,
    apply_settled_turn,
    has_open_questions,
    invoke_ticket_studio_model,
    latest_pending_studio_turn,
)
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

INTERRUPTED_TURN_MESSAGE = (
    "The scoper was interrupted by a server restart and did not finish this turn. Run it again."
)


def _latest_user_content(session: Session, session_id: str) -> str:
    latest_user = session.exec(
        select(TicketStudioMessage)
        .where(
            TicketStudioMessage.session_id == session_id,
            TicketStudioMessage.role == "user",
        )
        .order_by(col(TicketStudioMessage.created_at).desc())
        .limit(1)
    ).first()
    return latest_user.content if latest_user else ""


def _settle(
    session: Session,
    assistant: TicketStudioMessage,
    *,
    content: str,
    status: str,
    parts_json: str = EMPTY_PARTS_JSON,
) -> None:
    assistant.content = content
    assistant.status = status
    assistant.parts_json = parts_json
    session.add(assistant)
    session.commit()


def execute_studio_turn_background(assistant_id: str) -> None:
    """Fresh-session background execution; mirrors the other three chat surfaces."""
    try:
        with Session(engine) as session:
            assistant = session.get(TicketStudioMessage, assistant_id)
            if not assistant:
                logger.error("Background studio turn not found: %s", assistant_id)
                return
            row = session.get(TicketStudioSession, assistant.session_id)
            if not row:
                _settle(
                    session,
                    assistant,
                    content="Ticket studio assistant unavailable: session not found",
                    status="failed",
                )
                return

            mode = assistant.turn_mode or STUDIO_TURN_CHAT
            prompt_mode = STUDIO_PROMPT_MODES.get(mode, "chat")
            latest_user_message = _latest_user_content(session, row.id)
            try:
                reply = invoke_ticket_studio_model(
                    session, row, latest_user_message, mode=prompt_mode
                )
            except Exception as exc:
                logger.exception("Ticket studio turn failed: %s", assistant_id)
                # Same wording the blocking path used, so the panel reads the same;
                # the status is what changed — a failure is now recorded as one.
                _settle(
                    session,
                    assistant,
                    content=f"Ticket studio assistant unavailable: {exc}",
                    status="failed",
                )
                return

            _settle(
                session,
                assistant,
                content=reply,
                status="complete",
                parts_json=parts_json_for_reply(session, reply, workspace_id=row.workspace_id),
            )
            apply_settled_turn(session, row, reply, mode=mode)
            row.updated_at = utcnow()
            session.add(row)
            session.commit()

            if mode == STUDIO_TURN_BOOTSTRAP_CLARIFY and not has_open_questions(row):
                _chain_scope_turn(session, row)
    except Exception:
        # Never leave the row pending: a stuck `pending` blocks every later turn on
        # this session, which is exactly the deadlock this design exists to prevent.
        logger.exception("Background studio turn crashed: %s", assistant_id)
        try:
            with Session(engine) as session:
                assistant = session.get(TicketStudioMessage, assistant_id)
                if assistant and assistant.status == "pending":
                    _settle(
                        session,
                        assistant,
                        content="Ticket studio assistant unavailable: internal error",
                        status="failed",
                    )
        except Exception:
            logger.exception("Failed to settle studio turn %s after crash", assistant_id)


def _chain_scope_turn(session: Session, row: TicketStudioSession) -> None:
    """Nothing was unclear — generate the breakdown without a second round trip.

    Queued as its own turn rather than run inline so it is recoverable like any
    other, and so the panel sees the session go busy again instead of appearing
    idle for the length of a scope call.
    """
    if latest_pending_studio_turn(session, row.id):
        return
    prompt = (
        "Generate the full ticket breakdown for this feature. "
        "Output tickets in the JSON scope block."
    )
    session.add(TicketStudioMessage(session_id=row.id, role="user", content=prompt))
    assistant = TicketStudioMessage(
        session_id=row.id,
        role="assistant",
        content="",
        status="pending",
        turn_mode=STUDIO_TURN_SCOPE,
    )
    session.add(assistant)
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(assistant)
    schedule_studio_turn(assistant.id)


def schedule_studio_turn(assistant_id: str) -> None:
    """Queue turn execution without blocking the API request thread."""
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        execute_studio_turn_background(assistant_id)
        return
    thread = threading.Thread(
        target=execute_studio_turn_background,
        args=(assistant_id,),
        name=f"loregarden-studio-turn-{assistant_id[:8]}",
        daemon=True,
    )
    thread.start()


def fail_interrupted_studio_turns(
    session: Session, *, message: str = INTERRUPTED_TURN_MESSAGE
) -> list[TicketStudioMessage]:
    """Settle turns orphaned by a restart so no session stays stuck working."""
    orphaned = session.exec(
        select(TicketStudioMessage).where(TicketStudioMessage.status == "pending")
    ).all()
    settled: list[TicketStudioMessage] = []
    for assistant in orphaned:
        assistant.content = message
        assistant.status = "failed"
        session.add(assistant)
        settled.append(assistant)
    if settled:
        session.commit()
    return settled
