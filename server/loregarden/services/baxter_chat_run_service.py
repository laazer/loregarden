"""Background execution for Home Baxter chat turns.

Mirrors ``branch_triage_run_service.py``. Home chat cannot reuse ``AgentRun``
either — it is ticket-scoped through a non-nullable FK, and a Home conversation
has no ticket — so the turn's lifecycle lives on the assistant
``BaxterChatMessage`` row itself (``status``). That keeps it durable: an
interrupted turn is settled at startup instead of leaving the composer stuck
behind a promise that will never resolve.
"""

from __future__ import annotations

import logging
import os
import threading

from loregarden.db.session import engine
from loregarden.models.domain import BaxterChatMessage, BaxterChatSession, Workspace
from loregarden.services.baxter_chat_service import (
    UNTITLED_SESSION_TITLE,
    derive_session_title,
    invoke_baxter_chat_model,
    latest_pending_turn,
    list_chat_messages,
    touch_chat_session,
)
from loregarden.services.chat_primitives import EMPTY_PARTS_JSON, parts_json_for_reply
from loregarden.services.cli_auth_errors import format_agent_unavailable
from loregarden.services.triage_service import TRIAGE_AGENT_NAME
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

INTERRUPTED_TURN_MESSAGE = (
    f"{TRIAGE_AGENT_NAME} was interrupted by a server restart and did not finish this turn. "
    "Send the message again."
)


class BaxterChatConflictError(ValueError):
    """Raised when a turn can't start because one is already in flight for the thread."""


def start_baxter_chat_turn(
    session: Session, chat_session: BaxterChatSession, content: str
) -> tuple[BaxterChatMessage, BaxterChatMessage]:
    """Persist the user message and a pending assistant row, then return.

    Executes nothing — call ``schedule_baxter_chat_turn(assistant.id)`` next.
    """
    text = content.strip()
    if not text:
        raise ValueError("Message content is required")

    if latest_pending_turn(session, chat_session.id):
        raise BaxterChatConflictError(
            f"{TRIAGE_AGENT_NAME} is still working on this conversation — wait for the "
            "current turn to finish."
        )

    user_message = BaxterChatMessage(
        session_id=chat_session.id,
        role="user",
        content=text,
        status="complete",
    )
    assistant_message = BaxterChatMessage(
        session_id=chat_session.id,
        role="assistant",
        content="",
        status="pending",
    )
    session.add(user_message)
    session.add(assistant_message)
    # The opening message names the thread, so the archive is useful before the
    # first reply lands rather than only after it.
    if not chat_session.title or chat_session.title == UNTITLED_SESSION_TITLE:
        chat_session.title = derive_session_title(text)
    session.add(chat_session)
    session.commit()
    session.refresh(user_message)
    session.refresh(assistant_message)
    touch_chat_session(session, chat_session)
    return user_message, assistant_message


def _settle(
    session: Session,
    assistant_id: str,
    *,
    content: str,
    status: str,
    parts_json: str = EMPTY_PARTS_JSON,
) -> BaxterChatMessage | None:
    assistant = session.get(BaxterChatMessage, assistant_id)
    if not assistant:
        logger.error("Baxter chat assistant message not found: %s", assistant_id)
        return None
    assistant.content = content
    assistant.status = status
    assistant.parts_json = parts_json
    session.add(assistant)
    session.commit()
    chat_session = session.get(BaxterChatSession, assistant.session_id)
    if chat_session:
        touch_chat_session(session, chat_session)
    return assistant


def _latest_user_content(session: Session, session_id: str) -> str:
    latest_user = session.exec(
        select(BaxterChatMessage)
        .where(
            BaxterChatMessage.session_id == session_id,
            BaxterChatMessage.role == "user",
        )
        .order_by(col(BaxterChatMessage.created_at).desc())
        .limit(1)
    ).first()
    return latest_user.content if latest_user else ""


def execute_baxter_chat_turn_background(assistant_id: str) -> None:
    """Fresh-session background execution; mirrors ``execute_branch_triage_turn_background``."""
    try:
        with Session(engine) as session:
            assistant = session.get(BaxterChatMessage, assistant_id)
            if not assistant:
                logger.error("Background Baxter chat turn not found: %s", assistant_id)
                return
            chat_session = session.get(BaxterChatSession, assistant.session_id)
            workspace = session.get(Workspace, chat_session.workspace_id) if chat_session else None
            if not chat_session or not workspace:
                _settle(
                    session,
                    assistant_id,
                    content=f"{TRIAGE_AGENT_NAME} unavailable: workspace not found",
                    status="failed",
                )
                return

            latest_user_message = _latest_user_content(session, chat_session.id)
            # Settled rows only, so the pending assistant row is not fed back as history.
            history = list_chat_messages(session, chat_session.id)
            try:
                reply = invoke_baxter_chat_model(
                    session,
                    workspace,
                    content=latest_user_message,
                    history=history,
                )
            except Exception as exc:
                logger.exception("Baxter chat turn failed: %s", assistant_id)
                _settle(
                    session,
                    assistant_id,
                    content=format_agent_unavailable(TRIAGE_AGENT_NAME, exc),
                    status="failed",
                )
                return
            _settle(
                session,
                assistant_id,
                content=reply,
                status="complete",
                parts_json=parts_json_for_reply(session, reply, workspace_id=workspace.id),
            )
    except Exception:
        # Never leave the row pending: a stuck `pending` disables the composer, which is
        # exactly the deadlock this design exists to prevent.
        logger.exception("Background Baxter chat turn crashed: %s", assistant_id)
        try:
            with Session(engine) as session:
                assistant = session.get(BaxterChatMessage, assistant_id)
                if assistant and assistant.status == "pending":
                    _settle(
                        session,
                        assistant_id,
                        content=f"{TRIAGE_AGENT_NAME} unavailable: internal error",
                        status="failed",
                    )
        except Exception:
            logger.exception("Failed to settle Baxter chat turn %s after crash", assistant_id)


def schedule_baxter_chat_turn(assistant_id: str) -> None:
    """Queue turn execution without blocking the API request thread."""
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        execute_baxter_chat_turn_background(assistant_id)
        return
    thread = threading.Thread(
        target=execute_baxter_chat_turn_background,
        args=(assistant_id,),
        name=f"loregarden-baxter-chat-{assistant_id[:8]}",
        daemon=True,
    )
    thread.start()


def fail_interrupted_baxter_chat_turns(
    session: Session, *, message: str = INTERRUPTED_TURN_MESSAGE
) -> list[BaxterChatMessage]:
    """Settle turns orphaned by a restart so no Home chat stays stuck working."""
    orphaned = session.exec(
        select(BaxterChatMessage).where(BaxterChatMessage.status == "pending")
    ).all()
    settled: list[BaxterChatMessage] = []
    for assistant in orphaned:
        assistant.content = message
        assistant.status = "failed"
        session.add(assistant)
        settled.append(assistant)
    if settled:
        session.commit()
    return settled
