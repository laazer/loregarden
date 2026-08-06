"""Home-scoped Baxter chat — persisted conversations on the workspace runtime.

Threads live in ``baxter_chat_sessions`` / ``baxter_chat_messages`` so a reload,
a navigation, or a server restart does not lose the conversation, and so the
archive has real threads to list. History is read from the database rather than
replayed by the client: a client-supplied history is unverifiable and drifts
from what the thread actually contains.

A turn runs through ``agent_turn_runner``: Claude uses the permission bridge,
other adapters use advisory or writable oneshot depending on intent. Unlike
triage there is no work item, so runs and approvals are workspace-scoped
(see ``ApprovalScope.for_workspace``).
"""

from __future__ import annotations

import json
from dataclasses import replace

from loregarden.agents.executors.permission_bridge import HOME_CHAT_STAGE_KEY
from loregarden.agents.registry import get_agent
from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    BaxterChatMessage,
    BaxterChatSession,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.agent_turn_runner import (
    AgentTurnRequest,
    capabilities_for_workspace,
    run_agent_turn,
)
from loregarden.services.approval_views import approval_to_view
from loregarden.services.chat_primitives import load_parts_json
from loregarden.services.cli_agent_runner import stub_response
from loregarden.services.cli_settings import (
    VALID_CLI_ADAPTERS,
    apply_runtime_overrides,
    parse_runtime_settings,
    resolve_effective_adapter,
    validated_effort_pins,
)
from loregarden.services.run_concurrency import find_active_workspace_chat_run
from loregarden.services.triage_service import (
    TRIAGE_AGENT_ID,
    TRIAGE_AGENT_NAME,
    TRIAGE_CLI_PROFILE,
)
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

# Posted by the chat UI Run button on an agent execution plan. Must stay in sync
# with ``agentPlanExecuteMessage`` in the client TodoListPrimitive.
AGENT_PLAN_EXECUTE_PREFIX = "Execute this agent execution plan now."


def is_agent_plan_execute_message(content: str) -> bool:
    """True when the operator pressed Run on an agent ``todo_list`` plan."""
    return content.lstrip().startswith(AGENT_PLAN_EXECUTE_PREFIX)


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


def list_home_chat_pending_approvals(
    session: Session, chat_session: BaxterChatSession
) -> list[dict]:
    """Pending permission/question cards for this Home thread's in-flight turn.

    Approvals raised by Home chat are workspace-scoped (``stage_key=home-chat``).
    They belong on the thread that currently holds the pending assistant row —
    idle threads must not inherit another conversation's asks.
    """
    if not latest_pending_turn(session, chat_session.id):
        return []
    active = find_active_workspace_chat_run(
        session, chat_session.workspace_id, stage_key=HOME_CHAT_STAGE_KEY
    )
    query = (
        select(Approval)
        .where(
            Approval.workspace_id == chat_session.workspace_id,
            Approval.status == ApprovalStatus.PENDING,
            Approval.stage_key == HOME_CHAT_STAGE_KEY,
        )
        .order_by(col(Approval.created_at).asc())
    )
    if active is not None:
        query = query.where(Approval.run_id == active.id)
    rows = list(session.exec(query).all())
    return [approval_to_view(session, item) for item in rows]


def baxter_chat_run_status(
    session: Session, chat_session: BaxterChatSession
) -> tuple[str, str | None]:
    """Return (run_status, active_turn_id) for the thread's latest turn."""
    pending = latest_pending_turn(session, chat_session.id)
    if not pending:
        return "idle", None
    active = find_active_workspace_chat_run(
        session, chat_session.workspace_id, stage_key=HOME_CHAT_STAGE_KEY
    )
    if active is not None and active.status == RunStatus.AWAITING_PERMISSION:
        return "awaiting_input", pending.id
    return "running", pending.id


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
    run_status, active_turn_id = baxter_chat_run_status(session, chat_session)
    workspace = session.get(Workspace, chat_session.workspace_id)
    caps = {
        "adapter": "claude",
        "permission_bridge": True,
        "inbox_approvals": True,
        "plan_execute": True,
        "stream_thinking": True,
        "steer": True,
    }
    if workspace:
        effective = apply_runtime_overrides(workspace, chat_session.runtime_json)
        caps = capabilities_for_workspace(effective, agent_adapter="claude").as_dict()
    return {
        "id": chat_session.id,
        "workspace_id": chat_session.workspace_id,
        "title": chat_session.title or UNTITLED_SESSION_TITLE,
        "messages": [_message_view(message) for message in messages],
        "pending_approvals": list_home_chat_pending_approvals(session, chat_session),
        "runtime": parse_runtime_settings(chat_session.runtime_json).model_dump(),
        "adapter_capabilities": caps,
        "run_status": run_status,
        "active_turn_id": active_turn_id,
        "created_at": chat_session.created_at.isoformat(),
        "updated_at": chat_session.updated_at.isoformat(),
    }


def get_chat_runtime(chat_session: BaxterChatSession) -> WorkspaceRuntimeSettings:
    return parse_runtime_settings(chat_session.runtime_json)


def set_chat_runtime(
    session: Session, chat_session: BaxterChatSession, runtime: WorkspaceRuntimeUpdate
) -> WorkspaceRuntimeSettings:
    if runtime.cli_adapter not in VALID_CLI_ADAPTERS:
        raise ValueError(f"Invalid cli_adapter: {runtime.cli_adapter}")
    payload = {
        "cli_adapter": runtime.cli_adapter,
        "claude_model": runtime.claude_model.strip(),
        "cursor_model": runtime.cursor_model.strip(),
        "codex_model": runtime.codex_model.strip(),
        "lmstudio_base_url": runtime.lmstudio_base_url.strip(),
        "lmstudio_model": runtime.lmstudio_model.strip(),
        **validated_effort_pins(runtime),
    }
    chat_session.runtime_json = json.dumps(payload)
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return get_chat_runtime(chat_session)


def build_baxter_chat_prompt(
    *,
    workspace: Workspace,
    history: list[BaxterChatMessage],
    latest_user_message: str,
    approvals: list[Approval],
    tickets: list[Ticket],
    interactive: bool = False,
    approval_bridge: bool = False,
) -> str:
    sections = [
        "# Baxter — Home chat",
        "",
        f"Workspace: {workspace.slug}",
        "",
    ]
    if interactive:
        sections.extend(
            [
                "You have real tool access in this workspace — file read/write, shell, and the "
                "Loregarden MCP tools where the runtime supports them.",
                "Investigate before answering: read code, run tests, reproduce failures.",
                "When you find an actionable fix, make it directly rather than only describing it.",
                "This channel is not scoped to a work item, so no ticket is implied — name the "
                "ticket explicitly on any MCP call that needs one.",
            ]
        )
        if approval_bridge:
            sections.append(
                "Destructive or high-risk actions route through Loregarden's approval prompt "
                "automatically — request them when needed rather than avoiding the work."
            )
        else:
            sections.append(
                "This turn runs on the operator's selected CLI (not a Claude-only bridge). "
                "Workspace writes are enabled; stay inside the repo and prefer reversible changes."
            )
        sections.append("")
    else:
        sections.extend(
            [
                "You are advisory only in this channel — do not claim to have executed tools "
                "or changed the repo.",
                "",
            ]
        )
    sections.append("## Live snapshot")
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
            "Kinds: thinking, ticket, ticket_workflow, parent_ticket, ticket_list,",
            "status_column, kanban, filterable_kanban, agent, workflow, gate,",
            "terminal, edit, calendar, calendar_event, workspace, todo_list,",
            "branch_history, commit, qa, giphy.",
            "Rules:",
            "- Never invent ticket/agent ids. Only reference ids from Active tickets",
            "  above, or ids returned by MCP after you create/look them up.",
            "- `ticket` / `ticket_list` / `kanban` cards are for existing tickets only.",
            "- When outlining work you will do (a build/fix plan), emit an agent",
            '  execution plan — `todo_list` with owner "agent" and title',
            '  "Agent execution plan". Do not fake a ticket card for unfiled work.',
            '  Example: ```loregarden\\n{"primitive":"todo_list","owner":"agent",'
            '"title":"Agent execution plan","items":[{"id":"api","text":"Add history API",'
            '"checked":false}]}\\n```',
            "- The UI shows Run on that card. When the operator sends",
            f'  "{AGENT_PLAN_EXECUTE_PREFIX}…", do the unchecked steps',
            "  with tools on whatever CLI they selected — do not only restate",
            "  the plan, and do not claim you need Claude. Re-emit the same",
            "  todo_list with checked:true as steps finish.",
            "- To ask the operator before proceeding, emit `qa`.",
            "- After creating a ticket via MCP, emit `ticket` with the real returned id.",
            "",
            "Reply as Baxter. Keep it concise.",
        ]
    )
    return "\n".join(sections)


class BaxterChatConflictError(ValueError):
    """Raised when a Home chat turn can't start because one is already running."""


def invoke_baxter_chat_model(
    session: Session,
    workspace: Workspace,
    *,
    content: str,
    history: list[BaxterChatMessage] | None = None,
    turn_id: str = "",
) -> str:
    """Run one Home chat turn against the workspace's current CLI/model runtime.

    ``turn_id`` is the pending assistant row this turn will settle onto. Passing
    it streams the agent's reasoning to that turn's thinking channel; omitting
    it runs the turn silently, which is all the one-shot adapters can do anyway.
    """
    message = (content or "").strip()
    if not message:
        raise ValueError("Message content is required")

    stub = stub_response(BAXTER_CHAT_CLI_PROFILE)
    if stub is not None:
        return stub

    agent = get_agent(TRIAGE_AGENT_ID) or {}
    selected = resolve_effective_adapter(
        agent_adapter=agent.get("adapter", "claude"), workspace=workspace
    )
    # Same intent map as every other chat surface: Claude turns execute via the
    # bridge; other adapters stay advisory unless the operator pressed Run.
    wants_execute = is_agent_plan_execute_message(message)
    intent = "execute" if selected == "claude" or wants_execute else "advisory"
    use_bridge = selected == "claude"

    prompt = build_baxter_chat_prompt(
        workspace=workspace,
        history=list(history or []),
        latest_user_message=message,
        approvals=_pending_approvals(session, workspace.id),
        tickets=_active_tickets(session, workspace.id),
        interactive=intent == "execute",
        approval_bridge=use_bridge,
    )
    result = run_agent_turn(
        AgentTurnRequest(
            session=session,
            workspace=workspace,
            prompt=prompt,
            profile=BAXTER_CHAT_CLI_PROFILE,
            agent=agent,
            intent=intent,
            adapter=selected,
            user_prompt=DEFAULT_BAXTER_CHAT_USER_PROMPT,
            turn_id=turn_id,
            stage_key=HOME_CHAT_STAGE_KEY,
            agent_id=TRIAGE_AGENT_ID,
            manage_run=intent == "execute",
            workspace_stage_key=HOME_CHAT_STAGE_KEY,
            claude_model_env="LOREGARDEN_BAXTER_CHAT_CLAUDE_MODEL",
            conflict_error=lambda msg: BaxterChatConflictError(
                f"{TRIAGE_AGENT_NAME} is still working on the previous message — wait for it to finish."
            ),
            track_workflow_stage=False,
        )
    )
    return result.reply
