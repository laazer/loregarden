"""Ticket-scoped triage chat with full work-item context."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loregarden.agents.mcp_context import build_mcp_triage_context
from loregarden.agents.registry import get_agent
from loregarden.core.workflow_loader import expand_gate_checklist
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalStatus,
    RunStatus,
    Ticket,
    TriageMessage,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.services.agent_turn_runner import (
    AdapterCapabilities,
    TurnIntent,
    adapter_capabilities,
    resolve_chat_intent,
)
from loregarden.services.approval_views import approval_to_view
from loregarden.services.chat_primitives import load_parts_json, parts_json_for_reply
from loregarden.services.chat_thinking import ChatTurnThinkingSink
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    run_cli_agent_turn,
    stub_response,
)
from loregarden.services.cli_auth_errors import format_agent_unavailable
from loregarden.services.cli_settings import (
    VALID_CLI_ADAPTERS,
    apply_runtime_overrides,
    parse_runtime_settings,
    resolve_chat_adapter,
)
from loregarden.services.hierarchy_service import collect_ticket_scope_ids
from sqlmodel import Session, col, select

TRIAGE_AGENT_ID = "triage"
TRIAGE_AGENT_NAME = "Baxter"
MAX_TRIAGE_HISTORY_MESSAGES = 12
MAX_TRIAGE_MESSAGE_CHARS = 2000
MAX_TRIAGE_DESCRIPTION_CHARS = 4000

TRIAGE_CLI_PROFILE = CliAgentProfile(
    agent_id=TRIAGE_AGENT_ID,
    assistant_label=TRIAGE_AGENT_NAME,
    cli_label="Triage",
    stub_env="LOREGARDEN_TRIAGE_STUB_RESPONSE",
    timeout_env="LOREGARDEN_TRIAGE_TIMEOUT",
    tmp_prefix="loregarden-triage-",
    reply_cap=8000,
)


def get_triage_runtime(ticket: Ticket) -> WorkspaceRuntimeSettings:
    return parse_runtime_settings(ticket.triage_runtime_json)


def set_triage_runtime(
    session: Session,
    ticket: Ticket,
    body: WorkspaceRuntimeUpdate,
) -> WorkspaceRuntimeSettings:
    if body.cli_adapter not in VALID_CLI_ADAPTERS:
        raise ValueError(f"Invalid cli_adapter: {body.cli_adapter}")
    payload = {
        "cli_adapter": body.cli_adapter,
        "claude_model": body.claude_model.strip(),
        "cursor_model": body.cursor_model.strip(),
        "codex_model": body.codex_model.strip(),
        "lmstudio_base_url": body.lmstudio_base_url.strip(),
        "lmstudio_model": body.lmstudio_model.strip(),
    }
    ticket.triage_runtime_json = json.dumps(payload)
    ticket.updated_at = datetime.now(timezone.utc)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return get_triage_runtime(ticket)


def apply_triage_runtime_overrides(workspace: Workspace, ticket: Ticket) -> Workspace:
    return apply_runtime_overrides(workspace, ticket.triage_runtime_json)


def list_triage_messages(
    session: Session, ticket_id: str, *, limit: int = 200
) -> list[TriageMessage]:
    return list(
        session.exec(
            select(TriageMessage)
            .where(TriageMessage.ticket_id == ticket_id)
            .order_by(TriageMessage.created_at.asc())
            .limit(limit)
        ).all()
    )


def list_ticket_approvals(session: Session, ticket_id: str) -> tuple[list[dict], list[dict]]:
    scope_ids = collect_ticket_scope_ids(session, ticket_id)
    pending = session.exec(
        select(Approval)
        .where(col(Approval.ticket_id).in_(scope_ids), Approval.status == ApprovalStatus.PENDING)
        .order_by(Approval.created_at.asc())
    ).all()
    recent = session.exec(
        select(Approval)
        .where(col(Approval.ticket_id).in_(scope_ids), Approval.status != ApprovalStatus.PENDING)
        .order_by(Approval.resolved_at.desc(), Approval.created_at.desc())
        .limit(24)
    ).all()
    return (
        [approval_to_view(session, item) for item in pending],
        [approval_to_view(session, item) for item in recent],
    )


def triage_run_status(session: Session, ticket_id: str) -> tuple[str, str | None]:
    """Return (run_status, active_run_id) for the ticket's latest triage turn."""
    latest_run = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket_id, AgentRun.agent_id == TRIAGE_AGENT_ID)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    ).first()
    if not latest_run:
        return "idle", None
    if latest_run.status == RunStatus.AWAITING_PERMISSION:
        return "awaiting_input", latest_run.id
    if latest_run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
        return "running", latest_run.id
    return "idle", None


def resolve_chat_capabilities(override_json: str = "") -> tuple[AdapterCapabilities, TurnIntent]:
    """What a Baxter rail can actually do, resolved the same way the turn
    executor resolves it.

    Published on the snapshots so the operator can see whether Baxter can act
    before they ask it to. An advisory rail and an executing rail are
    indistinguishable in the transcript until a turn fails to do the thing it
    was asked for.
    """
    agent = get_agent(TRIAGE_AGENT_ID) or {}
    adapter = resolve_chat_adapter(
        agent_adapter=agent.get("adapter", "claude"),
        override_json=override_json,
    )
    return adapter_capabilities(adapter), resolve_chat_intent(adapter)


def triage_snapshot(session: Session, ticket: Ticket) -> dict:
    pending, recent = list_ticket_approvals(session, ticket.id)
    messages = list_triage_messages(session, ticket.id)
    run_status, active_run_id = triage_run_status(session, ticket.id)
    capabilities, intent = resolve_chat_capabilities(ticket.triage_runtime_json)
    return {
        "pending_approvals": pending,
        "recent_approvals": recent,
        "adapter_capabilities": capabilities.as_dict(),
        "chat_intent": intent,
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "parts": load_parts_json(msg.parts_json),
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
        "runtime": get_triage_runtime(ticket).model_dump(),
        "run_status": run_status,
        "active_run_id": active_run_id,
    }


def send_triage_message(session: Session, ticket: Ticket, content: str) -> dict:
    text = content.strip()
    if not text:
        raise ValueError("Message cannot be empty")

    user_message = TriageMessage(ticket_id=ticket.id, role="user", content=text)
    session.add(user_message)
    ticket.revision += 1
    ticket.updated_at = datetime.now(timezone.utc)
    session.add(ticket)
    session.commit()
    session.refresh(user_message)

    try:
        reply = invoke_triage_model(session, ticket, text)
    except Exception as exc:
        reply = format_agent_unavailable(TRIAGE_AGENT_NAME, exc)

    assistant_message = TriageMessage(
        ticket_id=ticket.id,
        role="assistant",
        content=reply,
        parts_json=parts_json_for_reply(session, reply, workspace_id=ticket.workspace_id),
    )
    session.add(assistant_message)
    session.add(ticket)
    session.commit()
    session.refresh(assistant_message)

    return {
        "user_message": {
            "id": user_message.id,
            "role": user_message.role,
            "content": user_message.content,
            "created_at": user_message.created_at.isoformat(),
        },
        "assistant_message": {
            "id": assistant_message.id,
            "role": assistant_message.role,
            "content": assistant_message.content,
            "parts": load_parts_json(assistant_message.parts_json),
            "created_at": assistant_message.created_at.isoformat(),
        },
    }


def invoke_triage_model(
    session: Session,
    ticket: Ticket,
    latest_user_message: str,
    *,
    run_id: str = "",
) -> str:
    stub = stub_response(TRIAGE_CLI_PROFILE)
    if stub is not None:
        return stub

    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise ValueError("Ticket workspace not found")

    history = list_triage_messages(session, ticket.id)
    prompt = build_triage_prompt(ticket, history, latest_user_message, session=session)
    effective = apply_triage_runtime_overrides(workspace, ticket)
    # The run is the turn here — `triage_run_status` publishes this same id as
    # `active_run_id`, which is what the panel watches.
    thinking = ChatTurnThinkingSink(run_id) if run_id else None
    try:
        return run_cli_agent_turn(
            TRIAGE_CLI_PROFILE,
            workspace=effective,
            prompt=prompt,
            run_id=run_id,
            workspace_slug=effective.slug or workspace.slug or "",
            read_only=True,
            thinking_sink=thinking,
        )
    finally:
        if thinking:
            thinking.close()


def current_human_gate_stage(session: Session, ticket: Ticket):
    """The ticket's current stage def, if it is an agentless human verification gate."""
    from loregarden.services.studio_routing import is_agentless_stage
    from loregarden.services.workflow_service import resolve_ticket_stages

    if not ticket.workflow_stage_key or ticket.workflow_stage_key == "done":
        return None
    _, stages = resolve_ticket_stages(session, ticket)
    stage = next((s for s in stages if s.key == ticket.workflow_stage_key), None)
    if not stage or not is_agentless_stage(stage):
        return None
    return stage


def _gate_focus_guidance(stage) -> str:
    label = f"{stage.key} {stage.name}".lower()
    if "playtest" in label or "gameplay" in label:
        return (
            "This is a gameplay playtest. Focus on how the change plays, not just whether "
            "automated tests pass: launch the relevant scene or build, exercise the change "
            "hands-on, and judge feel, balance, and regressions in adjacent systems against "
            "the acceptance criteria."
        )
    if "ux" in label or "usability" in label or "user" in label:
        return (
            "This is a human UX verification. Focus on the user experience: drive the "
            "affected flows end-to-end and check layout, copy, affordances, and error "
            "states against the acceptance criteria."
        )
    return (
        "This is a human verification step. Walk the acceptance criteria hands-on and "
        "confirm the change behaves correctly in practice, not just in automated tests."
    )


def build_gate_triage_sections(session: Session, ticket: Ticket) -> list[str]:
    stage = current_human_gate_stage(session, ticket)
    if stage is None:
        return []
    sections = [
        "",
        f"## Human verification gate: {stage.name}",
        f"The ticket is parked at the '{stage.name}' stage — a human sign-off gate, "
        "not an agent stage. Your job in this conversation is to help the operator run "
        "that verification: reproduce what they see, diagnose issues, and fix what you can.",
        _gate_focus_guidance(stage),
    ]
    checklist = expand_gate_checklist(ticket, list(stage.checklist or []))
    if checklist:
        sections.append("Verification checklist for this gate:")
        sections.extend(f"- {item}" for item in checklist)
    sections.append(
        "Fixes made during this verification are prototypes. When the operator is "
        "satisfied, they resolve the gate from the approvals panel — approving it "
        "forward, or approving it with a route back to an earlier workflow stage so "
        "prototype changes get rebuilt properly with production code and tests."
    )
    return sections


def build_advisory_sections(advisory_reason: str) -> list[str]:
    """What to tell a rail that has no tools this turn.

    A one-shot advisory turn ends with its reply: there is no later message in
    which announced work happens. An answer opening "I'll check X, then I'll do
    Y" therefore reads to the operator as a promise that was silently dropped —
    which is what made this rail feel broken rather than merely limited.
    """
    sections = [
        "You are advisory only in this channel — you have no tools. Do not claim to have "
        "executed tools or changed the repo.",
        "Do not announce work you are about to do — no 'I'll check…', 'I'll inspect…', "
        "'let me look at…'. This reply is the whole turn; nothing runs after it. "
        "Answer from what you already have.",
        "When the operator asks for an action you cannot take, say so in one line and "
        "name who or what can take it — do not narrate an attempt.",
    ]
    if advisory_reason:
        # Naming the cause turns "I can't do that" into something the operator
        # can act on, and stops the model inventing a reason of its own.
        sections.append(f"Why this channel is advisory: {advisory_reason}")
    return sections


def build_triage_prompt(
    ticket: Ticket,
    history: list[TriageMessage],
    latest_user_message: str,
    *,
    session: Session,
    interactive: bool = False,
    advisory_reason: str = "",
) -> str:
    workspace = session.get(Workspace, ticket.workspace_id)
    ac = json.loads(ticket.acceptance_criteria_json or "[]")

    runs = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id, AgentRun.agent_id != TRIAGE_AGENT_ID)
        .order_by(AgentRun.created_at.desc())
        .limit(5)
    ).all()

    sections = [
        "# Loregarden ticket triage",
        "You are Baxter, the operator's triage assistant for this work item.",
        "Help clarify requirements, interpret agent output, suggest next workflow steps, and answer questions.",
    ]
    if interactive:
        sections.extend(
            [
                "You have real tool access in this workspace — file read/write, Bash, and the Loregarden MCP tools.",
                "Investigate proactively: read code, run tests/lints, and reproduce failures before answering.",
                "When you find an actionable fix, make it directly rather than only describing it.",
                "Ask the operator a clarifying question (via AskUserQuestion) whenever the ticket, "
                "acceptance criteria, or a requested change is ambiguous — do not guess on anything "
                "consequential or hard to reverse.",
                "Destructive or high-risk actions still route through Loregarden's approval prompt "
                "automatically — request them when needed rather than avoiding the work.",
            ]
        )
    else:
        sections.extend(build_advisory_sections(advisory_reason))
    sections.extend(build_gate_triage_sections(session, ticket))
    sections.append("")
    if workspace:
        sections.extend(
            [
                build_mcp_triage_context(
                    ticket=ticket, workspace=workspace, interactive=interactive
                ),
                "",
            ]
        )
    description = (ticket.description or "—")[:MAX_TRIAGE_DESCRIPTION_CHARS]
    if ticket.description and len(ticket.description) > MAX_TRIAGE_DESCRIPTION_CHARS:
        description += "…"
    sections.extend(
        [
            f"Ticket: {ticket.external_id} — {ticket.title}",
            f"State: {ticket.state.value}",
            f"Workflow stage: {ticket.workflow_stage_key} ({ticket.workflow_stage_status.value})",
            f"Blocking issues: {ticket.blocking_issues or 'None'}",
            "",
            "## Description",
            description,
            "",
            "## Acceptance criteria",
            *([f"- {item}" for item in ac] if ac else ["- None"]),
        ]
    )

    if ticket.blocking_issues:
        sections.extend(["", "## Blocking issues", ticket.blocking_issues])

    if runs:
        sections.extend(["", "## Recent runs"])
        for run in reversed(runs):
            sections.append(
                f"- {run.run_code} · {run.stage_key} · {run.agent_id} · {run.status.value}"
            )
            if run.stderr:
                sections.append(f"  stderr: {run.stderr[:400]}")

    if history:
        sections.extend(["", "## Triage conversation so far"])
        for msg in history[-MAX_TRIAGE_HISTORY_MESSAGES:]:
            speaker = "Operator" if msg.role == "user" else TRIAGE_AGENT_NAME
            content = msg.content
            if len(content) > MAX_TRIAGE_MESSAGE_CHARS:
                content = content[:MAX_TRIAGE_MESSAGE_CHARS] + "…"
            sections.append(f"{speaker}: {content}")

    sections.extend(["", "## Latest operator message", latest_user_message, "", "Reply concisely."])
    return "\n".join(sections)
