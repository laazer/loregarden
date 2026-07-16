"""Background execution for branch triage chat turns.

Mirrors ``triage_run_service.py``, which does the same for ticket triage. Branch
chats cannot reuse ``AgentRun``: it is ticket-scoped via a non-nullable FK, and a
branch often has no linked ticket. The turn's lifecycle therefore lives on the
assistant ``BranchTriageMessage`` row itself (``status``), which keeps the state
durable — an interrupted turn is recoverable at startup instead of stranding the
UI on a promise that never settles.
"""

from __future__ import annotations

import logging
import os
import threading

from loregarden.db.session import engine
from loregarden.models.domain import BranchTriageMessage, Workspace
from loregarden.services.branch_triage_chat_service import (
    invoke_branch_triage_model,
    latest_pending_turn,
)
from loregarden.services.triage_service import TRIAGE_AGENT_NAME
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

INTERRUPTED_TURN_MESSAGE = (
    f"{TRIAGE_AGENT_NAME} was interrupted by a server restart and did not finish this turn. "
    "Send the message again."
)


class BranchTriageConflictError(ValueError):
    """Raised when a turn can't start because one is already in flight for the branch."""


def start_branch_triage_run(
    session: Session, workspace: Workspace, branch: str, content: str
) -> tuple[BranchTriageMessage, BranchTriageMessage]:
    """Persist the user message and a pending assistant row, then return.

    Executes nothing — call ``schedule_branch_triage_turn(assistant.id)`` next.
    """
    text = content.strip()
    if not text:
        raise ValueError("Message cannot be empty")

    if latest_pending_turn(session, workspace.id, branch):
        raise BranchTriageConflictError(
            f"{TRIAGE_AGENT_NAME} is still working on this branch — wait for the current "
            "turn to finish."
        )

    user_message = BranchTriageMessage(
        workspace_id=workspace.id,
        branch=branch,
        role="user",
        content=text,
        status="complete",
    )
    assistant_message = BranchTriageMessage(
        workspace_id=workspace.id,
        branch=branch,
        role="assistant",
        content="",
        status="pending",
    )
    session.add(user_message)
    session.add(assistant_message)
    session.commit()
    session.refresh(user_message)
    session.refresh(assistant_message)
    return user_message, assistant_message


def _settle(
    session: Session, assistant_id: str, *, content: str, status: str
) -> BranchTriageMessage | None:
    assistant = session.get(BranchTriageMessage, assistant_id)
    if not assistant:
        logger.error("Branch triage assistant message not found: %s", assistant_id)
        return None
    assistant.content = content
    assistant.status = status
    session.add(assistant)
    session.commit()
    return assistant


def execute_branch_triage_turn_background(assistant_id: str) -> None:
    """Fresh-session background execution; mirrors ``execute_triage_turn_background``."""
    try:
        with Session(engine) as session:
            assistant = session.get(BranchTriageMessage, assistant_id)
            if not assistant:
                logger.error("Background branch triage turn not found: %s", assistant_id)
                return
            workspace = session.get(Workspace, assistant.workspace_id)
            if not workspace:
                _settle(
                    session,
                    assistant_id,
                    content=f"{TRIAGE_AGENT_NAME} unavailable: workspace not found",
                    status="failed",
                )
                return

            branch = assistant.branch
            latest_user_message = _latest_user_content(session, workspace.id, branch)
            try:
                reply = invoke_branch_triage_model(session, workspace, branch, latest_user_message)
                _settle(session, assistant_id, content=reply, status="complete")
            except Exception as exc:
                logger.exception("Branch triage turn failed: %s", assistant_id)
                _settle(
                    session,
                    assistant_id,
                    content=f"{TRIAGE_AGENT_NAME} unavailable: {exc}",
                    status="failed",
                )
    except Exception:
        # Never leave the row pending: a stuck `pending` disables the composer, which is
        # exactly the deadlock this design exists to prevent.
        logger.exception("Background branch triage turn crashed: %s", assistant_id)
        try:
            with Session(engine) as session:
                assistant = session.get(BranchTriageMessage, assistant_id)
                if assistant and assistant.status == "pending":
                    _settle(
                        session,
                        assistant_id,
                        content=f"{TRIAGE_AGENT_NAME} unavailable: internal error",
                        status="failed",
                    )
        except Exception:
            logger.exception("Failed to settle branch triage turn %s after crash", assistant_id)


def _latest_user_content(session: Session, workspace_id: str, branch: str) -> str:
    latest_user = session.exec(
        select(BranchTriageMessage)
        .where(
            BranchTriageMessage.workspace_id == workspace_id,
            BranchTriageMessage.branch == branch,
            BranchTriageMessage.role == "user",
        )
        .order_by(BranchTriageMessage.created_at.desc())
        .limit(1)
    ).first()
    return latest_user.content if latest_user else ""


def schedule_branch_triage_turn(assistant_id: str) -> None:
    """Queue turn execution without blocking the API request thread."""
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        execute_branch_triage_turn_background(assistant_id)
        return
    thread = threading.Thread(
        target=execute_branch_triage_turn_background,
        args=(assistant_id,),
        name=f"loregarden-branch-triage-{assistant_id[:8]}",
        daemon=True,
    )
    thread.start()


def fail_interrupted_branch_triage_turns(
    session: Session, *, message: str = INTERRUPTED_TURN_MESSAGE
) -> list[BranchTriageMessage]:
    """Settle turns orphaned by a restart so no branch chat stays stuck working."""
    orphaned = session.exec(
        select(BranchTriageMessage).where(BranchTriageMessage.status == "pending")
    ).all()
    settled: list[BranchTriageMessage] = []
    for assistant in orphaned:
        assistant.content = message
        assistant.status = "failed"
        session.add(assistant)
        settled.append(assistant)
    if settled:
        session.commit()
    return settled
