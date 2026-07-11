"""Branch-scoped triage chat for the Branch Triage tool."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from loregarden.agents.cli_adapters import (
    DEFAULT_BRANCH_TRIAGE_USER_PROMPT,
    build_triage_invocation,
)
from loregarden.agents.registry import get_agent
from loregarden.models.domain import (
    BranchTriageMessage,
    Ticket,
    Workspace,
    WorkspaceRuntimeSettings,
)
from loregarden.services.branch_triage_service import branch_triage_snapshot
from loregarden.services.cli_output import extract_triage_reply
from loregarden.services.triage_service import (
    TRIAGE_AGENT_ID,
    TRIAGE_AGENT_NAME,
    apply_triage_runtime_overrides,
    get_triage_runtime,
    resolve_triage_timeout,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

MAX_BRANCH_TRIAGE_HISTORY = 12
MAX_BRANCH_TRIAGE_MESSAGE_CHARS = 2000


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
    return list(
        session.exec(
            select(BranchTriageMessage)
            .where(
                BranchTriageMessage.workspace_id == workspace_id,
                BranchTriageMessage.branch == branch,
            )
            .order_by(BranchTriageMessage.created_at.asc())
            .limit(limit)
        ).all()
    )


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
    }


def send_branch_triage_message(
    session: Session, workspace: Workspace, branch: str, content: str
) -> dict:
    text = content.strip()
    if not text:
        raise ValueError("Message cannot be empty")

    user_message = BranchTriageMessage(
        workspace_id=workspace.id,
        branch=branch,
        role="user",
        content=text,
    )
    session.add(user_message)
    session.commit()
    session.refresh(user_message)

    try:
        reply = invoke_branch_triage_model(session, workspace, branch, text)
    except Exception as exc:
        reply = f"{TRIAGE_AGENT_NAME} unavailable: {exc}"

    assistant_message = BranchTriageMessage(
        workspace_id=workspace.id,
        branch=branch,
        role="assistant",
        content=reply,
    )
    session.add(assistant_message)
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
    stub = os.environ.get("LOREGARDEN_TRIAGE_STUB_RESPONSE")
    if stub is not None:
        return stub

    ticket = _linked_ticket(session, workspace.id, branch)
    effective_workspace = apply_triage_runtime_overrides(workspace, ticket) if ticket else workspace

    repo_root = resolve_workspace_root(effective_workspace)
    if not repo_root.is_dir():
        raise ValueError(f"Workspace repo path does not exist: {repo_root}")

    agent = get_agent(TRIAGE_AGENT_ID)
    if not agent:
        raise ValueError(f"Unknown triage agent: {TRIAGE_AGENT_ID}")

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

    with tempfile.TemporaryDirectory(prefix="loregarden-branch-triage-") as tmp:
        prompt_file = Path(tmp) / "prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        invocation = build_triage_invocation(
            agent_id=TRIAGE_AGENT_ID,
            adapter=agent.get("adapter", "claude"),
            prompt=prompt,
            prompt_file=prompt_file,
            skill_name="",
            workspace_root=repo_root,
            workspace=effective_workspace,
            user_prompt=os.environ.get(
                "LOREGARDEN_BRANCH_TRIAGE_USER_PROMPT", DEFAULT_BRANCH_TRIAGE_USER_PROMPT
            ),
        )
        timeout = resolve_triage_timeout(agent)
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
            raise TimeoutError(f"{TRIAGE_AGENT_NAME} timed out after {timeout}s") from None

        if proc.returncode != 0:
            detail = (
                stderr.decode("utf-8", errors="replace").strip()
                or stdout.decode("utf-8", errors="replace").strip()
            )
            raise RuntimeError(detail or f"Triage CLI exited with code {proc.returncode}")

        reply = extract_triage_reply(stdout.decode("utf-8", errors="replace"))
        if not reply:
            raise RuntimeError(f"{TRIAGE_AGENT_NAME} returned an empty response")
        return reply[:8000]
