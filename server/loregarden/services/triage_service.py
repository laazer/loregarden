"""Ticket-scoped triage chat with full work-item context."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, col, select

from loregarden.agents.cli_adapters import resolve_cli_invocation
from loregarden.agents.registry import get_agent
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalStatus,
    Ticket,
    TriageMessage,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.agents.mcp_context import build_mcp_triage_context, load_loregarden_mcp_doc
from loregarden.services.approval_views import approval_to_view
from loregarden.services.hierarchy_service import collect_ticket_scope_ids
from loregarden.services.cli_settings import VALID_CLI_ADAPTERS
from loregarden.services.workspace_paths import resolve_agent_context_dir, resolve_workspace_root

TRIAGE_AGENT_ID = "triage"


def get_triage_runtime(ticket: Ticket) -> WorkspaceRuntimeSettings:
    data = json.loads(ticket.triage_runtime_json or "{}")
    return WorkspaceRuntimeSettings(
        cli_adapter=str(data.get("cli_adapter") or "default"),
        claude_model=str(data.get("claude_model") or ""),
        cursor_model=str(data.get("cursor_model") or ""),
        lmstudio_base_url=str(data.get("lmstudio_base_url") or ""),
        lmstudio_model=str(data.get("lmstudio_model") or ""),
    )


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
    overrides = json.loads(ticket.triage_runtime_json or "{}")
    if not overrides:
        return workspace
    data = workspace.model_dump()
    adapter = str(overrides.get("cli_adapter") or "default")
    if adapter != "default":
        data["cli_adapter"] = adapter
    for field in ("claude_model", "cursor_model", "lmstudio_base_url", "lmstudio_model"):
        value = str(overrides.get(field) or "").strip()
        if value:
            data[field] = value
    return Workspace.model_validate(data)


def list_triage_messages(session: Session, ticket_id: str, *, limit: int = 200) -> list[TriageMessage]:
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


def triage_snapshot(session: Session, ticket: Ticket) -> dict:
    pending, recent = list_ticket_approvals(session, ticket.id)
    messages = list_triage_messages(session, ticket.id)
    return {
        "pending_approvals": pending,
        "recent_approvals": recent,
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
        "runtime": get_triage_runtime(ticket).model_dump(),
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
        reply = f"Triage assistant unavailable: {exc}"

    assistant_message = TriageMessage(ticket_id=ticket.id, role="assistant", content=reply)
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
            "created_at": assistant_message.created_at.isoformat(),
        },
    }


def invoke_triage_model(session: Session, ticket: Ticket, latest_user_message: str) -> str:
    stub = os.environ.get("LOREGARDEN_TRIAGE_STUB_RESPONSE")
    if stub is not None:
        return stub

    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise ValueError("Ticket workspace not found")

    effective_workspace = apply_triage_runtime_overrides(workspace, ticket)

    repo_root = resolve_workspace_root(effective_workspace)
    if not repo_root.is_dir():
        raise ValueError(f"Workspace repo path does not exist: {repo_root}")

    agent = get_agent(TRIAGE_AGENT_ID)
    if not agent:
        raise ValueError(f"Unknown triage agent: {TRIAGE_AGENT_ID}")

    history = list_triage_messages(session, ticket.id)
    prompt = build_triage_prompt(ticket, history, latest_user_message, session=session)

    with tempfile.TemporaryDirectory(prefix="loregarden-triage-") as tmp:
        prompt_file = Path(tmp) / "prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        invocation = resolve_cli_invocation(
            agent_id=TRIAGE_AGENT_ID,
            adapter=agent.get("adapter", "claude"),
            prompt=prompt,
            prompt_file=prompt_file,
            skill_name="",
            workspace_root=repo_root,
            workspace=effective_workspace,
        )
        timeout = int(agent.get("timeout", 120))
        proc = subprocess.Popen(
            invocation.argv,
            cwd=invocation.cwd or str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if invocation.stdin_prompt else None,
            bufsize=0,
        )
        if invocation.stdin_prompt and proc.stdin:
            proc.stdin.write(invocation.stdin_prompt.encode("utf-8"))
            proc.stdin.close()
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise TimeoutError(f"Triage assistant timed out after {timeout}s") from None

        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"Triage CLI exited with code {proc.returncode}")

        reply = stdout.decode("utf-8", errors="replace").strip()
        if not reply:
            raise RuntimeError("Triage assistant returned an empty response")
        return reply[:8000]


def build_triage_prompt(
    ticket: Ticket,
    history: list[TriageMessage],
    latest_user_message: str,
    *,
    session: Session,
) -> str:
    from loregarden.services.workspace_paths import resolve_agent_context_dir

    workspace = session.get(Workspace, ticket.workspace_id)
    ac = json.loads(ticket.acceptance_criteria_json or "[]")

    runs = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id)
        .order_by(AgentRun.created_at.desc())
        .limit(5)
    ).all()

    sections = [
        "# Loregarden ticket triage",
        "You are the operator's triage assistant for this work item.",
        "Help clarify requirements, interpret agent output, suggest next workflow steps, and answer questions.",
        "You are advisory only in this channel — do not claim to have executed tools or changed the repo.",
        "",
    ]
    if workspace:
        sections.extend([build_mcp_triage_context(ticket=ticket, workspace=workspace), ""])
        agent_context_dir = resolve_agent_context_dir(workspace)
        mcp_doc = load_loregarden_mcp_doc(agent_context_dir)
        if mcp_doc:
            sections.extend(["## Loregarden MCP module", mcp_doc[:8000], ""])
    sections.extend(
        [
            f"Ticket: {ticket.external_id} — {ticket.title}",
            f"State: {ticket.state.value}",
            f"Workflow stage: {ticket.workflow_stage_key} ({ticket.workflow_stage_status.value})",
            f"Blocking issues: {ticket.blocking_issues or 'None'}",
            "",
            "## Description",
            ticket.description or "—",
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
        for msg in history:
            speaker = "Operator" if msg.role == "user" else "Triage assistant"
            sections.append(f"{speaker}: {msg.content}")

    sections.extend(["", "## Latest operator message", latest_user_message, "", "Reply concisely."])
    return "\n".join(sections)
