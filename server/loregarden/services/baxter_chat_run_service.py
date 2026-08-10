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

from loregarden.agents.executors.approval_scope import HOME_CHAT_STAGE_KEY
from loregarden.db.session import engine
from loregarden.models.domain import BaxterChatMessage, BaxterChatSession, Workspace
from loregarden.services.baxter_chat_service import (
    UNTITLED_SESSION_TITLE,
    BaxterChatConflictError,
    derive_session_title,
    invoke_baxter_chat_model,
    latest_pending_turn,
    list_chat_messages,
    touch_chat_session,
)
from loregarden.services.chat_primitives import EMPTY_PARTS_JSON, parts_json_for_reply
from loregarden.services.chat_thinking import finish_chat_turn_thinking, with_thinking_part
from loregarden.services.cli_auth_errors import format_agent_unavailable
from loregarden.services.cli_settings import apply_runtime_overrides
from loregarden.services.run_cancellation import request_cancel
from loregarden.services.run_concurrency import find_active_workspace_chat_run
from loregarden.services.triage_service import TRIAGE_AGENT_NAME
from loregarden.skills.registry import list_skills
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

INTERRUPTED_TURN_MESSAGE = (
    f"{TRIAGE_AGENT_NAME} was interrupted by a server restart and did not finish this turn. "
    "Send the message again."
)

CANCELLED_TURN_MESSAGE = f"{TRIAGE_AGENT_NAME} stopped this turn at your request."


def start_baxter_chat_turn(
    session: Session, chat_session: BaxterChatSession, content: str, *, skill_name: str = ""
) -> tuple[BaxterChatMessage, BaxterChatMessage]:
    """Persist the user message and a pending assistant row, then return.

    Executes nothing — call ``schedule_baxter_chat_turn(assistant.id)`` next.

    ``skill_name`` is the skill picked from the composer's `/` menu. It rides on
    the user row so the background worker reads it from the same place it reads
    the message — the request thread is long gone by then.
    """
    text = content.strip()
    if not text:
        raise ValueError("Message content is required")
    skill = skill_name.strip()
    if skill and skill not in list_skills():
        raise ValueError(f"Skill '{skill}' is not registered")

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
        skill_name=skill,
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
    # Always taken, including on the failure paths: what the agent was working
    # through when it died is the most useful thing a failed turn produced.
    thinking = finish_chat_turn_thinking(session, assistant_id)
    assistant = session.get(BaxterChatMessage, assistant_id)
    if not assistant:
        logger.error("Baxter chat assistant message not found: %s", assistant_id)
        return None
    # Operator stop (or a restart reap) may settle the row while the CLI is still
    # working — do not let a late success overwrite the cancelled turn.
    if assistant.status != "pending":
        return assistant
    assistant.content = content
    assistant.status = status
    assistant.parts_json = with_thinking_part(parts_json, thinking)
    session.add(assistant)
    session.commit()
    chat_session = session.get(BaxterChatSession, assistant.session_id)
    if chat_session:
        touch_chat_session(session, chat_session)
    return assistant


def _latest_user_message(session: Session, session_id: str) -> BaxterChatMessage | None:
    return session.exec(
        select(BaxterChatMessage)
        .where(
            BaxterChatMessage.session_id == session_id,
            BaxterChatMessage.role == "user",
        )
        .order_by(col(BaxterChatMessage.created_at).desc())
        .limit(1)
    ).first()


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

            latest_user = _latest_user_message(session, chat_session.id)
            effective_workspace = apply_runtime_overrides(workspace, chat_session.runtime_json)
            # Settled rows only, so the pending assistant row is not fed back as history.
            history = list_chat_messages(session, chat_session.id)
            try:
                reply = invoke_baxter_chat_model(
                    session,
                    effective_workspace,
                    content=latest_user.content if latest_user else "",
                    history=history,
                    turn_id=assistant_id,
                    skill_name=latest_user.skill_name if latest_user else "",
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


def cancel_baxter_chat_turn(
    session: Session,
    chat_session: BaxterChatSession,
    *,
    message: str = CANCELLED_TURN_MESSAGE,
) -> BaxterChatMessage | None:
    """Stop the in-flight Home chat turn and unlock the composer immediately.

    Settles the pending assistant row first (that is what ``run_status`` and the
    composer busy flag key on). Then asks any matching workspace ``AgentRun`` to
    stop cooperatively so an interactive Claude turn does not keep burning tokens.
    """
    pending = latest_pending_turn(session, chat_session.id)
    if not pending:
        return None

    settled = _settle(session, pending.id, content=message, status="failed")
    run = find_active_workspace_chat_run(
        session, chat_session.workspace_id, stage_key=HOME_CHAT_STAGE_KEY
    )
    if run:
        try:
            request_cancel(session, run)
        except ValueError:
            # Already cancelling or no longer in flight — the pending row is what
            # unlocks the composer; the run flag is best-effort.
            pass
    return settled
