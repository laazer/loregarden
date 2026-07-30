"""Home-scoped Baxter chat — persisted conversations on the workspace runtime.

Threads live in ``baxter_chat_sessions`` / ``baxter_chat_messages`` so a reload,
a navigation, or a server restart does not lose the conversation, and so the
archive has real threads to list. History is read from the database rather than
replayed by the client: a client-supplied history is unverifiable and drifts
from what the thread actually contains.
"""

from __future__ import annotations

from dataclasses import replace

from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    BaxterChatMessage,
    BaxterChatSession,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.chat_primitives import load_parts_json
from loregarden.services.cli_agent_runner import run_cli_agent_turn, stub_response
from loregarden.services.triage_service import TRIAGE_CLI_PROFILE
from sqlmodel import Session, col, select

BAXTER_CHAT_CLI_PROFILE = replace(
    TRIAGE_CLI_PROFILE,
    stub_env="LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE",
    timeout_env="LOREGARDEN_BAXTER_CHAT_TIMEOUT",
    tmp_prefix="loregarden-baxter-chat-",
)

MAX_HISTORY = 12
MAX_MESSAGE_CHARS = 2000
MAX_SNAPSHOT_ROWS = 8
MAX_TITLE_CHARS = 60
UNTITLED_SESSION_TITLE = "New chat"

DEFAULT_BAXTER_CHAT_USER_PROMPT = (
    "You are Baxter, the Home assistant for Loregarden. Answer from the live "
    "workspace snapshot and conversation. Be concise and actionable. Prefer "
    "concrete next steps over generic advice."
)


def _clip(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _pending_approvals(session: Session, workspace_id: str) -> list[Approval]:
    return list(
        session.exec(
            select(Approval)
            .where(
                Approval.workspace_id == workspace_id,
                Approval.status == ApprovalStatus.PENDING,
            )
            .order_by(col(Approval.created_at).desc())
            .limit(MAX_SNAPSHOT_ROWS)
        ).all()
    )


def _active_tickets(session: Session, workspace_id: str) -> list[Ticket]:
    return list(
        session.exec(
            select(Ticket)
            .where(
                Ticket.workspace_id == workspace_id,
                col(Ticket.state).in_([TicketState.IN_PROGRESS, TicketState.BLOCKED]),
            )
            .order_by(Ticket.priority.asc(), Ticket.updated_at.desc())
            .limit(MAX_SNAPSHOT_ROWS)
        ).all()
    )


def derive_session_title(text: str) -> str:
    """A thread's name, taken from its opening message.

    The archive needs something to show the moment a thread exists, and asking
    the operator to name a conversation before having it is friction no chat
    product survives. A rename overrides this permanently.
    """
    first_line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not first_line:
        return UNTITLED_SESSION_TITLE
    if len(first_line) <= MAX_TITLE_CHARS:
        return first_line
    return first_line[: MAX_TITLE_CHARS - 1].rstrip() + "…"


def list_chat_sessions(
    session: Session, workspace_id: str, *, limit: int = 50
) -> list[BaxterChatSession]:
    return list(
        session.exec(
            select(BaxterChatSession)
            .where(BaxterChatSession.workspace_id == workspace_id)
            .order_by(col(BaxterChatSession.updated_at).desc())
            .limit(limit)
        ).all()
    )


def create_chat_session(
    session: Session, workspace_id: str, *, title: str = ""
) -> BaxterChatSession:
    row = BaxterChatSession(
        workspace_id=workspace_id,
        title=(title or "").strip() or UNTITLED_SESSION_TITLE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_chat_session(
    session: Session, workspace_id: str, session_id: str
) -> BaxterChatSession | None:
    """Scoped by workspace on purpose: an id from another workspace is a 404, not a read."""
    row = session.get(BaxterChatSession, session_id)
    if not row or row.workspace_id != workspace_id:
        return None
    return row


def delete_chat_session(session: Session, chat_session: BaxterChatSession) -> None:
    for message in session.exec(
        select(BaxterChatMessage).where(BaxterChatMessage.session_id == chat_session.id)
    ).all():
        session.delete(message)
    session.delete(chat_session)
    session.commit()


def touch_chat_session(session: Session, chat_session: BaxterChatSession) -> None:
    """Bump the archive's ordering key. Called by turns, never by a rename."""
    chat_session.updated_at = utcnow()
    session.add(chat_session)
    session.commit()


def list_chat_messages(
    session: Session, session_id: str, *, limit: int = 500
) -> list[BaxterChatMessage]:
    """Settled messages only — a pending assistant row has no content yet and is
    surfaced through ``baxter_chat_run_status`` instead."""
    return list(
        session.exec(
            select(BaxterChatMessage)
            .where(
                BaxterChatMessage.session_id == session_id,
                BaxterChatMessage.status != "pending",
            )
            .order_by(col(BaxterChatMessage.created_at).asc())
            .limit(limit)
        ).all()
    )


def latest_pending_turn(session: Session, session_id: str) -> BaxterChatMessage | None:
    return session.exec(
        select(BaxterChatMessage)
        .where(
            BaxterChatMessage.session_id == session_id,
            BaxterChatMessage.status == "pending",
        )
        .order_by(col(BaxterChatMessage.created_at).desc())
        .limit(1)
    ).first()


def baxter_chat_run_status(session: Session, session_id: str) -> tuple[str, str | None]:
    """Return (run_status, active_turn_id) for the thread's latest turn."""
    pending = latest_pending_turn(session, session_id)
    if pending:
        return "running", pending.id
    return "idle", None


def _message_view(message: BaxterChatMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "parts": load_parts_json(message.parts_json),
        "created_at": message.created_at.isoformat(),
    }


def chat_session_summary(session: Session, chat_session: BaxterChatSession) -> dict:
    """One archive row: enough to recognise a thread without loading it."""
    messages = list_chat_messages(session, chat_session.id)
    last = messages[-1] if messages else None
    return {
        "id": chat_session.id,
        "title": chat_session.title or UNTITLED_SESSION_TITLE,
        "message_count": len(messages),
        "preview": _clip(last.content, 160) if last else "",
        "created_at": chat_session.created_at.isoformat(),
        "updated_at": chat_session.updated_at.isoformat(),
    }


def chat_session_snapshot(session: Session, chat_session: BaxterChatSession) -> dict:
    messages = list_chat_messages(session, chat_session.id)
    run_status, active_turn_id = baxter_chat_run_status(session, chat_session.id)
    return {
        "id": chat_session.id,
        "workspace_id": chat_session.workspace_id,
        "title": chat_session.title or UNTITLED_SESSION_TITLE,
        "messages": [_message_view(message) for message in messages],
        "run_status": run_status,
        "active_turn_id": active_turn_id,
        "created_at": chat_session.created_at.isoformat(),
        "updated_at": chat_session.updated_at.isoformat(),
    }


def build_baxter_chat_prompt(
    *,
    workspace: Workspace,
    history: list[BaxterChatMessage],
    latest_user_message: str,
    approvals: list[Approval],
    tickets: list[Ticket],
) -> str:
    sections = [
        "# Baxter — Home chat",
        "",
        f"Workspace: {workspace.slug}",
        "",
        "## Live snapshot",
    ]
    if approvals:
        sections.append(f"Pending approvals ({len(approvals)}):")
        for approval in approvals:
            title = approval.title or approval.tool_name or "Approval"
            sections.append(f"- {title} [{approval.kind.value}] ticket={approval.ticket_id}")
    else:
        sections.append("Pending approvals: none")

    if tickets:
        sections.append(f"Active tickets ({len(tickets)}):")
        for ticket in tickets:
            sections.append(
                f"- [{ticket.state.value}] p{ticket.priority} {ticket.external_id or ticket.id}: {ticket.title}"
            )
    else:
        sections.append("Active tickets: none")

    trimmed = history[-MAX_HISTORY:]
    if trimmed:
        sections.extend(["", "## Conversation"])
        for message in trimmed:
            role = message.role if message.role in {"user", "assistant"} else "user"
            sections.append(f"{role}: {_clip(message.content)}")

    sections.extend(
        [
            "",
            "## Latest operator message",
            _clip(latest_user_message),
            "",
            "## Chat UI primitives",
            "When a live card helps more than prose, emit a fenced `loregarden` JSON",
            "block with a `primitive` field. Prefer thin refs (ticket_id, agent_id).",
            'Example: ```loregarden\\n{"primitive":"ticket","ticket_id":"<id>"}\\n```',
            "Kinds: thinking, ticket, ticket_workflow, parent_ticket, ticket_list,",
            "status_column, kanban, filterable_kanban, agent, workflow, gate,",
            "terminal, edit, calendar, calendar_event.",
            "",
            "Reply as Baxter. Keep it concise.",
        ]
    )
    return "\n".join(sections)


def invoke_baxter_chat_model(
    session: Session,
    workspace: Workspace,
    *,
    content: str,
    history: list[BaxterChatMessage] | None = None,
) -> str:
    """Run one Home chat turn against the workspace's current CLI/model runtime."""
    message = (content or "").strip()
    if not message:
        raise ValueError("Message content is required")

    stub = stub_response(BAXTER_CHAT_CLI_PROFILE)
    if stub is not None:
        return stub

    prompt = build_baxter_chat_prompt(
        workspace=workspace,
        history=list(history or []),
        latest_user_message=message,
        approvals=_pending_approvals(session, workspace.id),
        tickets=_active_tickets(session, workspace.id),
    )
    return run_cli_agent_turn(
        BAXTER_CHAT_CLI_PROFILE,
        workspace=workspace,
        prompt=prompt,
        user_prompt=DEFAULT_BAXTER_CHAT_USER_PROMPT,
    )
