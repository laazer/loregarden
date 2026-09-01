"""Home-scoped Baxter chat — persisted conversations on the workspace runtime.

Threads live in ``baxter_chat_sessions`` / ``baxter_chat_messages`` so a reload,
a navigation, or a server restart does not lose the conversation, and so the
archive has real threads to list. History is read from the database rather than
replayed by the client: a client-supplied history is unverifiable and drifts
from what the thread actually contains.

A turn runs through ``agent_turn_runner``: intent comes from adapter
capabilities (permission bridge vs writable oneshot), not adapter-name
checks. Unlike triage there is no work item, so runs and approvals are
workspace-scoped (see ``ApprovalScope.for_workspace``). Execute-capable
adapters act on every turn — file writes and ordinary git included.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime

from loregarden.agents.chat_role_prompt import (
    AGENT_PLAN_EXECUTE_PREFIX,
    chat_posture_blocks,
    chat_role_blocks,
    chat_ui_primitives_blocks,
)
from loregarden.agents.executors.permission_bridge import HOME_CHAT_STAGE_KEY
from loregarden.agents.prompt_blocks import raw_block
from loregarden.agents.registry import get_agent
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalStatus,
    BaxterChatMessage,
    BaxterChatSession,
    ChatSurface,
    OrchestrationRun,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.models.domain.chat_primitives import TodoListPart
from loregarden.models.domain.enums import utcnow
from loregarden.services.agent_turn_runner import (
    AgentTurnRequest,
    adapter_capabilities,
    capabilities_for_workspace,
    resolve_chat_intent,
    run_agent_turn,
)
from loregarden.services.approval_views import approval_to_view
from loregarden.services.chat_mode import resolve_chat_mode
from loregarden.services.chat_primitives import load_parts_json, parse_primitive_parts
from loregarden.services.cli_agent_runner import stub_response
from loregarden.services.cli_settings import (
    VALID_CLI_ADAPTERS,
    apply_runtime_overrides,
    parse_runtime_settings,
    resolve_effective_adapter,
    validated_effort_pins,
)
from loregarden.services.run_concurrency import find_active_workspace_chat_run
from loregarden.services.stage_agent_view import ticket_stage_agent
from loregarden.services.studio_service import build_studio_prompt_sections
from loregarden.services.triage_service import (
    TRIAGE_AGENT_ID,
    TRIAGE_AGENT_NAME,
    TRIAGE_CLI_PROFILE,
)
from loregarden.skills.registry import get_skill, skill_prompt_block
from sqlmodel import Session, col, or_, select

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

# AGENT_PLAN_EXECUTE_PREFIX now lives in agents.chat_role_prompt, beside the UI
# primitives block that interpolates it, and is imported above.
MAX_AGENT_PLAN_ATTEMPTS = 4
_AGENT_PLAN_ID_PATTERN = re.compile(r'plan_id\s+["\']([^"\']+)["\']')


def is_agent_plan_execute_message(content: str) -> bool:
    """True when the operator pressed Run on an agent ``todo_list`` plan."""
    return content.lstrip().startswith(AGENT_PLAN_EXECUTE_PREFIX)


def _agent_plan_id(content: str) -> str:
    match = _AGENT_PLAN_ID_PATTERN.search(content)
    return match.group(1) if match else ""


def _agent_plan_reply_complete(reply: str, plan_id: str) -> bool | None:
    """Return completion, continuation, or operator-input state for a plan reply.

    ``None`` means the agent emitted a QA card and therefore needs the operator
    before it can continue. A missing plan card is incomplete: without the
    structured checklist the server cannot safely infer completion from prose.
    """
    matching_plan: TodoListPart | None = None
    for part in parse_primitive_parts(reply):
        if part.primitive == "qa":
            return None
        if part.primitive == "todo_list" and part.owner == "agent":
            if not plan_id or part.plan_id == plan_id:
                matching_plan = part
    if matching_plan is None or not matching_plan.items:
        return False
    return all(item.checked for item in matching_plan.items)


def _agent_plan_continuation_prompt(prompt: str, reply: str, plan_id: str) -> str:
    plan_label = f'plan_id "{plan_id}"' if plan_id else "the active agent plan"
    return "\n".join(
        [
            prompt,
            "",
            "## Execution continuation",
            f"The previous attempt returned before {plan_label} was verifiably complete.",
            "Continue working now. Do not merely describe the remaining work. End only by",
            "emitting the matching todo_list with every item checked, or a qa card that",
            "records the concrete blocker, required approval, or operator input.",
            "",
            "Previous attempt reply:",
            _clip(reply),
        ]
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


# Any id-shaped token: a ticket UUID, an ``external_id`` slug, a ``run_``/``orch_``
# code. One rule for all three — a token is worth a lookup if it is long enough to
# be an identifier and carries a separator, which ordinary prose does not. Matching
# is exact against indexed columns, so a token that is not an id simply finds
# nothing; the pattern only has to be cheap, not precise.
_REFERENCE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{5,}")
MAX_REFERENCE_TOKENS = 24
MAX_RESOLVED_REFERENCES = 5


@dataclass(frozen=True)
class ResolvedReferences:
    """Records the operator named by id, looked up before the turn runs.

    Not a substitute for MCP — an advisory turn *does* still carry the Loregarden
    tools, verified against a real ``codex exec --sandbox read-only`` run. This is
    a guarantee rather than a capability: the agent runs ``--cd`` the workspace
    checkout, which is a different repository from the control plane, so a model
    that decides to go looking instead of calling MCP finds nothing and invents a
    path. Putting the answer in the prompt removes that decision.
    """

    tickets: list[Ticket]
    agent_runs: list[AgentRun]
    orchestration_runs: list[OrchestrationRun]
    workspace_slugs: dict[str, str]
    #: ticket id -> the agent its current stage would dispatch, derived when
    #: the references were resolved. Precomputed here for the same reason as
    #: `workspace_slugs`: the prompt builders have no session, and the
    #: alternative was reading `ticket.next_agent`, a pin that is empty for
    #: most of a ticket's life — so the status line said "next agent: —" for
    #: tickets that had one.
    stage_agents: dict[str, str]

    def __bool__(self) -> bool:
        return bool(self.tickets or self.agent_runs or self.orchestration_runs)


def _reference_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _REFERENCE_TOKEN_PATTERN.finditer(text or ""):
        token = match.group(0).strip("._-")
        if len(token) < 6 or not ("-" in token or "_" in token):
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= MAX_REFERENCE_TOKENS:
            break
    return tokens


def resolve_references(session: Session, text: str) -> ResolvedReferences:
    """Look up every id-shaped token in the operator's message.

    Deliberately not workspace-scoped: an operator who pastes an id expects it
    resolved, and a Home chat that answers "not in my snapshot" to a real id is
    the failure this exists to prevent. Rows from another workspace are labelled
    with their slug rather than hidden.
    """
    tokens = _reference_tokens(text)
    if not tokens:
        return ResolvedReferences([], [], [], {}, {})

    tickets = list(
        session.exec(
            select(Ticket)
            .where(or_(col(Ticket.id).in_(tokens), col(Ticket.external_id).in_(tokens)))
            .limit(MAX_RESOLVED_REFERENCES)
        ).all()
    )
    agent_runs = list(
        session.exec(
            select(AgentRun)
            .where(or_(col(AgentRun.id).in_(tokens), col(AgentRun.run_code).in_(tokens)))
            .order_by(col(AgentRun.created_at).desc())
            .limit(MAX_RESOLVED_REFERENCES)
        ).all()
    )
    orchestration_runs = list(
        session.exec(
            select(OrchestrationRun)
            .where(
                or_(
                    col(OrchestrationRun.id).in_(tokens),
                    col(OrchestrationRun.run_code).in_(tokens),
                )
            )
            .order_by(col(OrchestrationRun.created_at).desc())
            .limit(MAX_RESOLVED_REFERENCES)
        ).all()
    )

    workspace_ids = {ticket.workspace_id for ticket in tickets}
    slugs = {
        workspace.id: workspace.slug
        for workspace in session.exec(
            select(Workspace).where(col(Workspace.id).in_(workspace_ids))
        ).all()
    }
    stage_agents = {ticket.id: ticket_stage_agent(session, ticket) for ticket in tickets}
    return ResolvedReferences(tickets, agent_runs, orchestration_runs, slugs, stage_agents)


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


def fork_chat_session(session: Session, source: BaxterChatSession) -> BaxterChatSession:
    """Branch a conversation: new session, same settled history, source untouched.

    Pending assistant rows are left behind on purpose — a fork is a place to
    continue from what has already been said, not from a turn still in flight.
    Runtime pins copy with the thread so the branch keeps the same model.
    """
    source_title = (source.title or "").strip() or UNTITLED_SESSION_TITLE
    fork_title = _clip(f"Fork of {source_title}", MAX_TITLE_CHARS)
    row = BaxterChatSession(
        workspace_id=source.workspace_id,
        title=fork_title,
        runtime_json=source.runtime_json or "{}",
    )
    session.add(row)
    session.flush()

    for message in list_chat_messages(session, source.id):
        session.add(
            BaxterChatMessage(
                session_id=row.id,
                role=message.role,
                content=message.content,
                status=message.status,
                parts_json=message.parts_json,
                skill_name=message.skill_name,
                created_at=message.created_at,
            )
        )

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
        # "" on every turn sent without a `/skill`, which is most of them.
        "skill_name": message.skill_name,
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
        "chat_mode": resolve_chat_mode(str(caps.get("adapter", "claude"))).as_dict(),
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


def _stamp(moment: datetime | None) -> str:
    return moment.isoformat(sep=" ", timespec="seconds") if moment else "—"


def _ticket_reference_lines(
    ticket: Ticket, workspace_slugs: dict[str, str], stage_agents: dict[str, str]
) -> list[str]:
    lines = [
        f"- ticket {ticket.id} ({ticket.external_id or 'no external id'}) "
        f"in workspace {workspace_slugs.get(ticket.workspace_id, ticket.workspace_id)}",
        f"  title: {ticket.title}",
        f"  state: {ticket.state.value} | stage: {ticket.workflow_stage_key or '—'}"
        f"/{ticket.workflow_stage_status.value} | next agent: "
        f"{stage_agents.get(ticket.id) or '—'}",
        f"  locked: {'yes' if ticket.state_locked else 'no'} | updated: {_stamp(ticket.updated_at)}",
    ]
    if ticket.blocking_issues:
        lines.append(f"  blocking issues: {_clip(ticket.blocking_issues, 400)}")
    return lines


def _reference_section(references: ResolvedReferences | None) -> list[str]:
    if not references:
        return []
    lines = ["", "## Resolved references", "Looked up from the ids in the operator's message."]
    for ticket in references.tickets:
        lines.extend(
            _ticket_reference_lines(ticket, references.workspace_slugs, references.stage_agents)
        )
    for run in references.agent_runs:
        lines.append(
            f"- agent run {run.run_code} (id {run.id}) agent={run.agent_id} "
            f"stage={run.stage_key or '—'} status={run.status.value} "
            f"started={_stamp(run.started_at)} finished={_stamp(run.finished_at)} "
            f"ticket={run.ticket_id or '—'}"
        )
    for run in references.orchestration_runs:
        lines.append(
            f"- orchestration {run.run_code} (id {run.id}) driver={run.driver.value} "
            f"status={run.status.value} stage={run.current_stage_key or '—'} "
            f"started={_stamp(run.started_at)} finished={_stamp(run.finished_at)} "
            f"ticket={run.ticket_id}"
        )
        if run.error_message:
            lines.append(f"  error: {_clip(run.error_message, 400)}")
    return lines


def build_baxter_chat_prompt(
    *,
    workspace: Workspace,
    history: list[BaxterChatMessage],
    latest_user_message: str,
    approvals: list[Approval],
    tickets: list[Ticket],
    agent: dict | None = None,
    references: ResolvedReferences | None = None,
    interactive: bool = False,
    approval_bridge: bool = False,
    skill_name: str = "",
) -> str:
    """Assemble one Home chat turn's system prompt.

    ``agent`` is the resolved agent config; its ``role_body`` is what an operator
    edits in Studio, and rendering it here is what makes that editor mean
    something on this rail. The caller passes it rather than the builder looking
    it up, so the builder stays pure and testable.
    """
    sections = [
        "# Baxter — Home chat",
        "",
        f"Workspace: {workspace.slug}",
        "",
    ]
    # The operator picked this skill from the composer's `/` menu for this turn.
    # It leads the prompt: a skill is instructions for *how* to do the work, so
    # it has to be read before the request it applies to.
    sections.extend(skill_prompt_block(skill_name, get_skill(skill_name) or ""))
    sections.extend(chat_role_blocks(agent or {}, surface=ChatSurface.HOME))
    sections.append("")
    sections.extend(
        chat_posture_blocks(
            surface=ChatSurface.HOME,
            interactive=interactive,
            approval_bridge=approval_bridge,
        )
    )
    if not interactive:
        sections.append(
            "Every id in the operator's message has already been resolved for you under "
            "Resolved references."
        )
    sections.append("")
    sections.extend(raw_block(build_studio_prompt_sections(agent or {})))
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
            # Both ids, always. Rendering only ``external_id`` left an operator who
            # pasted a ticket UUID unmatchable against a row that was right there.
            label = f"{ticket.external_id} (id {ticket.id})" if ticket.external_id else ticket.id
            sections.append(f"- [{ticket.state.value}] p{ticket.priority} {label}: {ticket.title}")
    else:
        sections.append("Active tickets: none")

    sections.extend(_reference_section(references))

    trimmed = history[-MAX_HISTORY:]
    if trimmed:
        sections.extend(["", "## Conversation"])
        for message in trimmed:
            role = message.role if message.role in {"user", "assistant"} else "user"
            sections.append(f"{role}: {_clip(message.content)}")

    sections.extend(["", "## Latest operator message", _clip(latest_user_message)])
    sections.extend(chat_ui_primitives_blocks())
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
    skill_name: str = "",
) -> str:
    """Run one Home chat turn against the workspace's current CLI/model runtime.

    ``turn_id`` is the pending assistant row this turn will settle onto. Passing
    it streams the agent's reasoning to that turn's thinking channel; omitting
    it runs the turn silently, which is all the one-shot adapters can do anyway.

    ``skill_name`` is the skill the operator picked from the composer's `/` menu
    for this turn; its body is rendered into the prompt the same way a stage
    run's skill is.
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
    caps = adapter_capabilities(selected)
    # Same as ticket triage: execute-capable adapters act. Cursor/Codex have no
    # inbox, but they run writable oneshot so the operator can get a code change
    # or a git action from a normal message, not only from pressing Run on a plan.
    intent = resolve_chat_intent(
        selected,
        wants_execute=is_agent_plan_execute_message(message),
    )
    interactive = intent == "execute"

    prompt = build_baxter_chat_prompt(
        workspace=workspace,
        history=list(history or []),
        latest_user_message=message,
        approvals=_pending_approvals(session, workspace.id),
        tickets=_active_tickets(session, workspace.id),
        agent=agent,
        references=resolve_references(session, message),
        interactive=interactive,
        approval_bridge=caps.permission_bridge,
        skill_name=skill_name,
    )
    request = AgentTurnRequest(
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
        manage_run=interactive,
        workspace_stage_key=HOME_CHAT_STAGE_KEY,
        claude_model_env="LOREGARDEN_BAXTER_CHAT_CLAUDE_MODEL",
        surface=ChatSurface.HOME,
        conflict_error=lambda msg: BaxterChatConflictError(
            f"{TRIAGE_AGENT_NAME} is still working on the previous message — wait for it to finish."
        ),
        track_workflow_stage=False,
    )
    result = run_agent_turn(request)
    if not interactive or not is_agent_plan_execute_message(message):
        return result.reply

    plan_id = _agent_plan_id(message)
    for _attempt in range(1, MAX_AGENT_PLAN_ATTEMPTS):
        completion = _agent_plan_reply_complete(result.reply, plan_id)
        if completion is True or completion is None:
            return result.reply
        request = replace(
            request,
            prompt=_agent_plan_continuation_prompt(prompt, result.reply, plan_id),
        )
        result = run_agent_turn(request)

    if _agent_plan_reply_complete(result.reply, plan_id) is not False:
        return result.reply
    raise RuntimeError(
        f"Agent execution plan did not reach completion after {MAX_AGENT_PLAN_ATTEMPTS} attempts"
    )
