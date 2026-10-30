"""CRUD for chat waits: register, outstanding lookup, cancel, continuation prompt.

The watcher (`chat_wait_watch`) starts continuation turns. This module does not
import the three run services, so they can import it without a cycle.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from loregarden.agents.executors.approval_scope import HOME_CHAT_STAGE_KEY
from loregarden.agents.executors.permission_bridge import BRANCH_TRIAGE_STAGE_KEY
from loregarden.models.domain import (
    AgentRun,
    BaxterChatMessage,
    BranchTriageMessage,
    ChatSurface,
    ProcessState,
    RunStatus,
    TriageMessage,
    Workspace,
)
from loregarden.models.domain.chat_wait_enums import ChatWaitRefuseReason, ChatWaitStatus
from loregarden.models.domain.chat_wait_tables import ChatWait
from loregarden.models.domain.enums import utcnow
from loregarden.services.process_identity import identify, session_of
from loregarden.services.run_concurrency import TRIAGE_STAGE_KEY
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

WAIT_CEILING_SECONDS = 7200
MAX_CHAT_WAIT_CONTINUATIONS = 3

_UNIT_SEP = "\x1f"
_THREAD_SURFACES = frozenset(
    {ChatSurface.HOME, ChatSurface.TICKET_TRIAGE, ChatSurface.BRANCH_TRIAGE}
)


@dataclass(frozen=True)
class ContinuationPayload:
    exit_code: int | None
    log_tail: str
    command: str
    log_path: str


def take_continuation(session: Session, run_id: str) -> ContinuationPayload | None:
    """Read the payload persisted on the wait before this continuation was scheduled."""
    wait = session.exec(select(ChatWait).where(ChatWait.continuation_run_id == run_id)).first()
    if wait is None or wait.log_tail is None:
        return None
    return ContinuationPayload(
        exit_code=wait.exit_code,
        log_tail=wait.log_tail,
        command=wait.command,
        log_path=wait.log_path,
    )


def attach_continuation_run(session: Session, wait: ChatWait, run_id: str) -> None:
    """Bind the minted run before schedule_* so execute can find the payload."""
    wait.continuation_run_id = run_id
    session.add(wait)
    session.commit()


def thread_key(
    surface: ChatSurface,
    *,
    ticket_id: str | None = None,
    chat_session_id: str | None = None,
    workspace_slug: str | None = None,
    branch: str | None = None,
) -> str:
    """The unique key for one chat thread on one surface."""
    if surface is ChatSurface.HOME:
        if not chat_session_id:
            raise ValueError("chat_session_id is required for HOME")
        return chat_session_id
    if surface is ChatSurface.TICKET_TRIAGE:
        if not ticket_id:
            raise ValueError("ticket_id is required for TICKET_TRIAGE")
        return ticket_id
    if surface is ChatSurface.BRANCH_TRIAGE:
        if not workspace_slug or not branch:
            raise ValueError("workspace_slug and branch are required for BRANCH_TRIAGE")
        return f"{workspace_slug}{_UNIT_SEP}{branch}"
    raise ValueError(f"chat wait is not offered on {surface.value}")


def outstanding_for(session: Session, surface: ChatSurface, key: str) -> ChatWait | None:
    return session.exec(
        select(ChatWait).where(
            ChatWait.surface == surface,
            ChatWait.thread_key == key,
            ChatWait.status == ChatWaitStatus.OUTSTANDING,
        )
    ).first()


def continued_count(session: Session, surface: ChatSurface, key: str) -> int:
    rows = session.exec(
        select(ChatWait).where(
            ChatWait.surface == surface,
            ChatWait.thread_key == key,
            ChatWait.status == ChatWaitStatus.CONTINUED,
        )
    ).all()
    return len(rows)


def wait_payload(wait: ChatWait) -> dict[str, str]:
    return {
        "command": wait.command,
        "log_path": wait.log_path,
        "registered_at": wait.registered_at.isoformat(),
        "deadline": wait.deadline.isoformat(),
    }


def continuation_blocks(
    exit_code: int | None, log_tail: str, command: str, log_path: str
) -> list[str]:
    """Prompt sections for a turn the control plane started because a wait exited."""
    exit_text = "unknown — no .rc file" if exit_code is None else str(exit_code)
    return [
        "## Background operation finished",
        "This is a new turn started by the control plane because a registered "
        "background operation exited. The operator did not send a message.",
        f"Command: {command}",
        f"Exit code: {exit_text}",
        f"Log path: {log_path}",
        "Log tail:",
        log_tail or "",
    ]


def stop_copy(command: str, log_path: str) -> str:
    return (
        f"Stopped waiting for {command} at your request. "
        f"The process was not killed — ask again, or watch {log_path}."
    )


def timeout_copy(command: str, log_path: str) -> str:
    return (
        f"Gave up waiting for {command} after 2 hours. "
        f"The process was not killed — ask again, or watch {log_path}."
    )


def cap_copy(command: str, log_path: str) -> str:
    return (
        f"This thread has already continued {MAX_CHAT_WAIT_CONTINUATIONS} times "
        f"from background work. {command} finished but another turn was not started "
        f"— ask again, or watch {log_path}."
    )


def boot_unknown_copy(command: str, log_path: str) -> str:
    return (
        f"Lost track of {command} after a server restart. "
        f"It may still be running — ask again, or watch {log_path}."
    )


def persist_system_message(session: Session, wait: ChatWait, content: str) -> None:
    """Write a Loregarden `role=system` row into the wait's thread."""
    if wait.surface is ChatSurface.TICKET_TRIAGE:
        session.add(TriageMessage(ticket_id=wait.thread_key, role="system", content=content))
        return
    if wait.surface is ChatSurface.HOME:
        session.add(
            BaxterChatMessage(
                session_id=wait.thread_key,
                role="system",
                content=content,
                status="complete",
            )
        )
        return
    slug, sep, branch = wait.thread_key.partition(_UNIT_SEP)
    if not sep:
        logger.warning("Branch wait %s has no unit-separator thread_key", wait.id)
        return
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        logger.warning(
            "Branch wait %s names workspace slug %r which is gone; system message not stored",
            wait.id,
            slug,
        )
        return
    session.add(
        BranchTriageMessage(
            workspace_id=workspace.id,
            branch=branch,
            role="system",
            content=content,
            status="complete",
        )
    )


def settle_wait(
    session: Session,
    wait: ChatWait,
    status: ChatWaitStatus,
    *,
    message: str | None = None,
    continued_index: int | None = None,
) -> None:
    wait.status = status
    if continued_index is not None:
        wait.continued_index = continued_index
    session.add(wait)
    if message is not None:
        persist_system_message(session, wait, message)
    session.commit()


def cancel_wait(session: Session, surface: ChatSurface, key: str) -> ChatWait | None:
    wait = outstanding_for(session, surface, key)
    if wait is None:
        return None
    settle_wait(
        session, wait, ChatWaitStatus.CANCELLED, message=stop_copy(wait.command, wait.log_path)
    )
    return wait


def _refuse(reason: ChatWaitRefuseReason) -> dict[str, Any]:
    return {"ok": False, "error_kind": reason.value}


def _locator_family(
    ticket_id: str | None,
    chat_session_id: str | None,
    workspace_slug: str | None,
    branch: str | None,
) -> ChatSurface | None:
    has_ticket = bool(ticket_id)
    has_home = bool(chat_session_id)
    has_slug = bool(workspace_slug)
    has_branch = bool(branch)
    has_branch_family = has_slug and has_branch
    families = sum((has_ticket, has_home, has_branch_family))
    if families != 1:
        return None
    if has_ticket:
        return ChatSurface.TICKET_TRIAGE if not (has_slug or has_branch) else None
    if has_home:
        return ChatSurface.HOME if not (has_slug or has_branch) else None
    if has_slug ^ has_branch:
        return None
    return ChatSurface.BRANCH_TRIAGE


def _bound_running_run(session: Session, run_id: str | None, stage_key: str) -> AgentRun | None:
    if not run_id:
        return None
    run = session.get(AgentRun, run_id)
    if run is None or run.stage_key != stage_key or run.status != RunStatus.RUNNING:
        return None
    return run


def _running_home_origin(session: Session, chat_session_id: str) -> AgentRun | None:
    pending = session.exec(
        select(BaxterChatMessage)
        .where(
            BaxterChatMessage.session_id == chat_session_id,
            BaxterChatMessage.status == "pending",
        )
        .order_by(col(BaxterChatMessage.created_at).desc())
        .limit(1)
    ).first()
    return _bound_running_run(session, pending.run_id if pending else None, HOME_CHAT_STAGE_KEY)


def _running_branch_origin(session: Session, workspace_slug: str, branch: str) -> AgentRun | None:
    workspace = session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
    if workspace is None:
        return None
    pending = session.exec(
        select(BranchTriageMessage)
        .where(
            BranchTriageMessage.workspace_id == workspace.id,
            BranchTriageMessage.branch == branch,
            BranchTriageMessage.status == "pending",
        )
        .order_by(col(BranchTriageMessage.created_at).desc())
        .limit(1)
    ).first()
    return _bound_running_run(session, pending.run_id if pending else None, BRANCH_TRIAGE_STAGE_KEY)


def _running_origin(
    session: Session,
    surface: ChatSurface,
    *,
    ticket_id: str | None,
    chat_session_id: str | None,
    workspace_slug: str | None,
    branch: str | None,
) -> AgentRun | None:
    if surface is ChatSurface.TICKET_TRIAGE and ticket_id:
        return session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == ticket_id,
                AgentRun.stage_key == TRIAGE_STAGE_KEY,
                AgentRun.status == RunStatus.RUNNING,
            )
        ).first()
    if surface is ChatSurface.HOME and chat_session_id:
        return _running_home_origin(session, chat_session_id)
    if surface is ChatSurface.BRANCH_TRIAGE and workspace_slug and branch:
        return _running_branch_origin(session, workspace_slug, branch)
    return None


def _detached_from_origin(pid: int, origin: AgentRun) -> bool:
    """True when pid's POSIX session is neither the server's nor the origin agent's."""
    child_sid = session_of(pid)
    if child_sid is None:
        return False
    if child_sid == os.getsid(os.getpid()):
        return False
    if origin.agent_pid is None:
        return False
    agent_sid = session_of(origin.agent_pid)
    return agent_sid is not None and child_sid != agent_sid


def register(
    session: Session,
    *,
    pid: int,
    command: str,
    log_path: str,
    ticket_id: str | None = None,
    chat_session_id: str | None = None,
    workspace_slug: str | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    """Record an outstanding wait for a detached child. Never spawns the child."""
    command = command.strip()
    log_path = log_path.strip()
    ticket_id = ticket_id.strip() if ticket_id else None
    chat_session_id = chat_session_id.strip() if chat_session_id else None
    workspace_slug = workspace_slug.strip() if workspace_slug else None
    branch = branch.strip() if branch else None

    if not command:
        return _refuse(ChatWaitRefuseReason.MISSING_COMMAND)
    if not log_path:
        return _refuse(ChatWaitRefuseReason.MISSING_LOG_PATH)

    surface = _locator_family(ticket_id, chat_session_id, workspace_slug, branch)
    if surface is None or surface not in _THREAD_SURFACES:
        return _refuse(ChatWaitRefuseReason.INVALID_LOCATOR)

    try:
        key = thread_key(
            surface,
            ticket_id=ticket_id,
            chat_session_id=chat_session_id,
            workspace_slug=workspace_slug,
            branch=branch,
        )
    except ValueError:
        return _refuse(ChatWaitRefuseReason.INVALID_LOCATOR)

    origin = _running_origin(
        session,
        surface,
        ticket_id=ticket_id,
        chat_session_id=chat_session_id,
        workspace_slug=workspace_slug,
        branch=branch,
    )
    if origin is None:
        return _refuse(ChatWaitRefuseReason.NO_IN_FLIGHT_TURN)

    if outstanding_for(session, surface, key) is not None:
        return _refuse(ChatWaitRefuseReason.ALREADY_WAITING)

    reading = identify(pid)
    if reading.state is not ProcessState.ALIVE or not reading.stamp:
        return _refuse(ChatWaitRefuseReason.NOT_ALIVE)

    if not _detached_from_origin(pid, origin):
        return _refuse(ChatWaitRefuseReason.SAME_SESSION)

    now = utcnow()
    wait = ChatWait(
        surface=surface,
        thread_key=key,
        origin_run_id=origin.id,
        pid=pid,
        pid_identity=reading.stamp,
        command=command,
        log_path=log_path,
        registered_at=now,
        deadline=now + timedelta(seconds=WAIT_CEILING_SECONDS),
        status=ChatWaitStatus.OUTSTANDING,
    )
    session.add(wait)
    session.commit()
    session.refresh(wait)
    return {"ok": True, "wait_id": wait.id, "deadline": wait.deadline.isoformat()}
