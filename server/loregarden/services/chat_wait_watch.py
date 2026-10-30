"""Poll outstanding chat waits and dispatch continuation or a system message.

Lives apart from `chat_wait` so the three run services can import CRUD without
importing this watcher (which imports them).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    ChatSurface,
    ProcessState,
    Ticket,
    Workspace,
)
from loregarden.models.domain.chat_wait_enums import ChatWaitStatus, ChatWaitUnknownPolicy
from loregarden.models.domain.chat_wait_tables import ChatWait
from loregarden.models.domain.enums import utcnow
from loregarden.services.baxter_chat_run_service import start_baxter_chat_continuation
from loregarden.services.branch_triage_run_service import start_branch_triage_continuation
from loregarden.services.chat_wait import (
    MAX_CHAT_WAIT_CONTINUATIONS,
    boot_unknown_copy,
    cap_copy,
    continued_count,
    settle_wait,
    timeout_copy,
)
from loregarden.services.process_identity import liveness
from loregarden.services.run_concurrency import IN_FLIGHT_STATUSES
from loregarden.services.run_output_files import read_output_text, recorded_exit_status
from loregarden.services.triage_run_service import start_triage_continuation
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

_LOG_TAIL_CHARS = 8000
_UNIT_SEP = "\x1f"


def _aware(stamp: datetime) -> datetime:
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def _past_deadline(wait: ChatWait, now: datetime) -> bool:
    return _aware(now) >= _aware(wait.deadline)


def _origin_in_flight(session: Session, wait: ChatWait) -> bool:
    origin = session.get(AgentRun, wait.origin_run_id)
    return origin is not None and origin.status in IN_FLIGHT_STATUSES


def _log_tail(log_path: str) -> str:
    return read_output_text(Path(log_path))[-_LOG_TAIL_CHARS:]


def _exit_code(log_path: str) -> int | None:
    return recorded_exit_status(Path(f"{log_path}.rc"))


def _continue_or_cap(session: Session, wait: ChatWait) -> None:
    if _origin_in_flight(session, wait):
        return
    if continued_count(session, wait.surface, wait.thread_key) >= MAX_CHAT_WAIT_CONTINUATIONS:
        settle_wait(
            session,
            wait,
            ChatWaitStatus.CAPPED,
            message=cap_copy(wait.command, wait.log_path),
        )
        return
    wait.exit_code = _exit_code(wait.log_path)
    wait.log_tail = _log_tail(wait.log_path)
    session.add(wait)
    session.commit()
    origin = session.get(AgentRun, wait.origin_run_id)
    if origin is None:
        logger.warning("Chat wait %s origin run %s is gone", wait.id, wait.origin_run_id)
        settle_wait(
            session,
            wait,
            ChatWaitStatus.INTERRUPTED,
            message=boot_unknown_copy(wait.command, wait.log_path),
        )
        return
    index = continued_count(session, wait.surface, wait.thread_key) + 1
    run = _start_continuation(session, wait, origin)
    if run is None:
        logger.warning("Chat wait %s could not start a continuation", wait.id)
        settle_wait(
            session,
            wait,
            ChatWaitStatus.INTERRUPTED,
            message=boot_unknown_copy(wait.command, wait.log_path),
        )
        return
    settle_wait(
        session,
        wait,
        ChatWaitStatus.CONTINUED,
        continued_index=index,
    )


def _start_continuation(session: Session, wait: ChatWait, origin: AgentRun) -> AgentRun | None:
    if wait.surface is ChatSurface.TICKET_TRIAGE:
        ticket = session.get(Ticket, wait.thread_key)
        if ticket is None:
            return None
        return start_triage_continuation(session, ticket, origin=origin, wait=wait)
    if wait.surface is ChatSurface.HOME:
        chat = session.get(BaxterChatSession, wait.thread_key)
        if chat is None:
            return None
        return start_baxter_chat_continuation(session, chat, origin=origin, wait=wait)
    slug, sep, branch = wait.thread_key.partition(_UNIT_SEP)
    if not sep:
        return None
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        return None
    return start_branch_triage_continuation(session, workspace, branch, origin=origin, wait=wait)


def _handle_wait(
    session: Session,
    wait: ChatWait,
    *,
    on_unknown: ChatWaitUnknownPolicy,
    now: datetime,
) -> None:
    if not wait.pid_identity:
        if on_unknown is ChatWaitUnknownPolicy.INTERRUPT:
            settle_wait(
                session,
                wait,
                ChatWaitStatus.INTERRUPTED,
                message=boot_unknown_copy(wait.command, wait.log_path),
            )
        elif _past_deadline(wait, now):
            settle_wait(
                session,
                wait,
                ChatWaitStatus.TIMED_OUT,
                message=timeout_copy(wait.command, wait.log_path),
            )
        return

    state = liveness(wait.pid, wait.pid_identity)
    if state is ProcessState.ALIVE:
        if _past_deadline(wait, now):
            settle_wait(
                session,
                wait,
                ChatWaitStatus.TIMED_OUT,
                message=timeout_copy(wait.command, wait.log_path),
            )
        return
    if state is ProcessState.GONE:
        _continue_or_cap(session, wait)
        return
    # UNKNOWN
    if on_unknown is ChatWaitUnknownPolicy.INTERRUPT:
        settle_wait(
            session,
            wait,
            ChatWaitStatus.INTERRUPTED,
            message=boot_unknown_copy(wait.command, wait.log_path),
        )
        return
    if _past_deadline(wait, now):
        settle_wait(
            session,
            wait,
            ChatWaitStatus.TIMED_OUT,
            message=timeout_copy(wait.command, wait.log_path),
        )


def poll_chat_waits(
    session: Session, *, on_unknown: ChatWaitUnknownPolicy = ChatWaitUnknownPolicy.SKIP
) -> int:
    """Timer path. UNKNOWN is never GONE; skip until the deadline, then timeout."""
    now = utcnow()
    waits = list(
        session.exec(select(ChatWait).where(ChatWait.status == ChatWaitStatus.OUTSTANDING)).all()
    )
    for wait in waits:
        _handle_wait(session, wait, on_unknown=on_unknown, now=now)
    return len(waits)


def resume_chat_waits(
    session: Session, *, on_unknown: ChatWaitUnknownPolicy = ChatWaitUnknownPolicy.INTERRUPT
) -> int:
    """Boot path. UNKNOWN / empty stamp write Boot UNKNOWN; ALIVE keeps watching."""
    return poll_chat_waits(session, on_unknown=on_unknown)
