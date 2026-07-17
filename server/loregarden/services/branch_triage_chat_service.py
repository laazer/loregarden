"""Branch-scoped triage chat for the Branch Triage tool."""

from __future__ import annotations

import os
from dataclasses import replace

from loregarden.agents.cli_adapters import DEFAULT_BRANCH_TRIAGE_USER_PROMPT
from loregarden.models.domain import (
    BranchTriageMessage,
    Ticket,
    Workspace,
    WorkspaceRuntimeSettings,
)
from loregarden.services.branch_triage_service import branch_triage_snapshot
from loregarden.services.cli_agent_runner import run_cli_agent_turn, stub_response
from loregarden.services.triage_service import (
    TRIAGE_AGENT_NAME,
    TRIAGE_CLI_PROFILE,
    apply_triage_runtime_overrides,
    get_triage_runtime,
)
from sqlmodel import Session, select

MAX_BRANCH_TRIAGE_HISTORY = 12
MAX_BRANCH_TRIAGE_MESSAGE_CHARS = 2000

# Same agent and limits as ticket triage; only the scratch directory differs.
BRANCH_TRIAGE_CLI_PROFILE = replace(TRIAGE_CLI_PROFILE, tmp_prefix="loregarden-branch-triage-")


def _linked_ticket(session: Session, workspace_id: str, branch: str) -> Ticket | None:
    for ticket in session.exec(select(Ticket).where(Ticket.workspace_id == workspace_id)).all():
        if (ticket.branch or "").strip() == branch:
            return ticket
    return None


def _branch_entry(session: Session, workspace: Workspace, branch: str) -> dict | None:
    snapshot = branch_triage_snapshot(session, workspace)
    for item in snapshot.get("branches", []):
        if item.get("name") == branch:
            return item
    return None


def list_branch_triage_messages(
    session: Session, workspace_id: str, branch: str, *, limit: int = 200
) -> list[BranchTriageMessage]:
    """Settled messages only. A pending assistant row has no content yet — it is a
    turn in flight, surfaced through ``branch_triage_run_status`` instead.
    """
    return list(
        session.exec(
            select(BranchTriageMessage)
            .where(
                BranchTriageMessage.workspace_id == workspace_id,
                BranchTriageMessage.branch == branch,
                BranchTriageMessage.status != "pending",
            )
            .order_by(BranchTriageMessage.created_at.asc())
            .limit(limit)
        ).all()
    )


def latest_pending_turn(
    session: Session, workspace_id: str, branch: str
) -> BranchTriageMessage | None:
    """The branch's in-flight turn, if any."""
    return session.exec(
        select(BranchTriageMessage)
        .where(
            BranchTriageMessage.workspace_id == workspace_id,
            BranchTriageMessage.branch == branch,
            BranchTriageMessage.status == "pending",
        )
        .order_by(BranchTriageMessage.created_at.desc())
        .limit(1)
    ).first()


def branch_triage_run_status(
    session: Session, workspace_id: str, branch: str
) -> tuple[str, str | None]:
    """Return (run_status, active_turn_id) for the branch's latest triage turn."""
    pending = latest_pending_turn(session, workspace_id, branch)
    if pending:
        return "running", pending.id
    return "idle", None


def _runtime_for_branch(
    session: Session, workspace: Workspace, ticket: Ticket | None
) -> WorkspaceRuntimeSettings:
    if ticket:
        return get_triage_runtime(ticket)
    data = {
        "cli_adapter": workspace.cli_adapter or "default",
        "claude_model": workspace.claude_model or "",
        "cursor_model": workspace.cursor_model or "",
        "lmstudio_base_url": workspace.lmstudio_base_url or "",
        "lmstudio_model": workspace.lmstudio_model or "",
    }
    return WorkspaceRuntimeSettings.model_validate(data)


def branch_chat_snapshot(session: Session, workspace: Workspace, branch: str) -> dict:
    ticket = _linked_ticket(session, workspace.id, branch)
    messages = list_branch_triage_messages(session, workspace.id, branch)
    run_status, active_turn_id = branch_triage_run_status(session, workspace.id, branch)
    return {
        "workspace_id": workspace.id,
        "branch": branch,
        "linked_ticket_id": ticket.id if ticket else None,
        "linked_ticket_external_id": ticket.external_id if ticket else None,
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
        "runtime": _runtime_for_branch(session, workspace, ticket).model_dump(),
        "run_status": run_status,
        "active_turn_id": active_turn_id,
    }


def build_branch_triage_prompt(
    workspace: Workspace,
    branch: str,
    branch_entry: dict | None,
    history: list[BranchTriageMessage],
    latest_user_message: str,
    *,
    ticket: Ticket | None,
) -> str:
    sections = [
        "# Loregarden branch triage",
        "You are Baxter, the operator's triage assistant for cleaning up git branches.",
        "You run in the workspace repository with shell and git access — execute commands when asked.",
        "When the operator requests git work (commit, push, checkout, merge, rebase, delete, etc.), run it and report exact outcomes.",
        "Use safe defaults: avoid force-push or branch deletion unless the operator clearly asks; confirm when intent is ambiguous.",
        "",
        f"Workspace: {workspace.name} ({workspace.slug})",
        f"Branch: {branch}",
    ]

    if branch_entry:
        sections.extend(
            [
                f"Base comparison: {branch_entry.get('ahead', 0)} ahead, {branch_entry.get('behind', 0)} behind",
                f"Dirty worktree: {'yes' if branch_entry.get('dirty') else 'no'}",
                f"Current checkout: {'yes' if branch_entry.get('is_current') else 'no'}",
            ]
        )
        last = branch_entry.get("last_commit") or {}
        if last.get("message"):
            sections.append(f"Last commit: {last.get('message')}")
        issues = branch_entry.get("issues") or []
        if issues:
            sections.extend(["", "## Detected issues"])
            for issue in issues:
                sections.append(f"- [{issue.get('severity', 'info')}] {issue.get('message')}")

    if ticket:
        sections.extend(
            [
                "",
                "## Linked work item",
                f"Ticket: {ticket.external_id} — {ticket.title}",
                f"State: {ticket.state.value}",
                f"Workflow: {ticket.workflow_stage_key} ({ticket.workflow_stage_status.value})",
            ]
        )
        if ticket.blocking_issues:
            sections.append(f"Blocking issues: {ticket.blocking_issues}")

    if history:
        sections.extend(["", "## Branch triage conversation so far"])
        for msg in history[-MAX_BRANCH_TRIAGE_HISTORY:]:
            speaker = "Operator" if msg.role == "user" else TRIAGE_AGENT_NAME
            body = msg.content
            if len(body) > MAX_BRANCH_TRIAGE_MESSAGE_CHARS:
                body = body[:MAX_BRANCH_TRIAGE_MESSAGE_CHARS] + "…"
            sections.append(f"{speaker}: {body}")

    sections.extend(["", "## Latest operator message", latest_user_message, "", "Reply concisely."])
    return "\n".join(sections)


def invoke_branch_triage_model(
    session: Session, workspace: Workspace, branch: str, latest_user_message: str
) -> str:
    stub = stub_response(BRANCH_TRIAGE_CLI_PROFILE)
    if stub is not None:
        return stub

    ticket = _linked_ticket(session, workspace.id, branch)
    history = list_branch_triage_messages(session, workspace.id, branch)
    branch_entry = _branch_entry(session, workspace, branch)
    prompt = build_branch_triage_prompt(
        workspace,
        branch,
        branch_entry,
        history,
        latest_user_message,
        ticket=ticket,
    )
    return run_cli_agent_turn(
        BRANCH_TRIAGE_CLI_PROFILE,
        workspace=apply_triage_runtime_overrides(workspace, ticket) if ticket else workspace,
        prompt=prompt,
        user_prompt=os.environ.get(
            "LOREGARDEN_BRANCH_TRIAGE_USER_PROMPT", DEFAULT_BRANCH_TRIAGE_USER_PROMPT
        ),
    )
