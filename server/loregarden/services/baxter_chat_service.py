"""Home-scoped Baxter chat — one-shot CLI/LM Studio turns on the workspace runtime."""

from __future__ import annotations

from dataclasses import replace

from loregarden.models.domain import Approval, ApprovalStatus, Ticket, TicketState, Workspace
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


def build_baxter_chat_prompt(
    *,
    workspace: Workspace,
    history: list[dict[str, str]],
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
            role = (message.get("role") or "user").strip().lower()
            if role not in {"user", "assistant"}:
                role = "user"
            sections.append(f"{role}: {_clip(message.get('content') or '')}")

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
    history: list[dict[str, str]] | None = None,
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
