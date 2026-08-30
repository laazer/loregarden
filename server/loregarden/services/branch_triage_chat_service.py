"""Branch-scoped triage chat for the Branch Triage tool."""

from __future__ import annotations

import os
from dataclasses import replace

from loregarden.agents.cli_adapters import DEFAULT_BRANCH_TRIAGE_USER_PROMPT
from loregarden.agents.executors.permission_bridge import BRANCH_TRIAGE_STAGE_KEY
from loregarden.agents.registry import get_agent
from loregarden.models.domain import (
    BranchTriageMessage,
    Ticket,
    Workspace,
    WorkspaceRuntimeSettings,
)
from loregarden.services.agent_turn_runner import (
    AgentTurnRequest,
    TurnIntent,
    run_agent_turn,
)
from loregarden.services.branch_triage_service import (
    branch_triage_snapshot,
    resolve_branch_checkout,
)
from loregarden.services.chat_mode import resolve_chat_mode
from loregarden.services.chat_primitives import load_parts_json
from loregarden.services.cli_agent_runner import stub_response
from loregarden.services.cli_settings import resolve_effective_adapter
from loregarden.services.triage_service import (
    TRIAGE_AGENT_ID,
    TRIAGE_AGENT_NAME,
    TRIAGE_CLI_PROFILE,
    apply_triage_runtime_overrides,
    get_triage_runtime,
    resolve_chat_capabilities,
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
        "codex_model": workspace.codex_model or "",
        "lmstudio_base_url": workspace.lmstudio_base_url or "",
        "lmstudio_model": workspace.lmstudio_model or "",
    }
    return WorkspaceRuntimeSettings.model_validate(data)


def branch_chat_snapshot(session: Session, workspace: Workspace, branch: str) -> dict:
    ticket = _linked_ticket(session, workspace.id, branch)
    messages = list_branch_triage_messages(session, workspace.id, branch)
    run_status, active_turn_id = branch_triage_run_status(session, workspace.id, branch)
    capabilities, intent = resolve_chat_capabilities(ticket.triage_runtime_json if ticket else "")
    # The checkout gate is knowable here, so the snapshot resolves it rather than
    # letting the UI promise a rail that will run read-only. The run-id gate is
    # not: there is no turn yet to have (or lack) a run.
    agent = get_agent(TRIAGE_AGENT_ID) or {}
    effective = apply_triage_runtime_overrides(workspace, ticket) if ticket else workspace
    mode = resolve_chat_mode(
        resolve_effective_adapter(
            agent_adapter=agent.get("adapter", "claude"), workspace=effective
        ),
        branch_checked_out=resolve_branch_checkout(workspace, branch) is not None,
    )
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
                "parts": load_parts_json(msg.parts_json),
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
        "runtime": _runtime_for_branch(session, workspace, ticket).model_dump(),
        "adapter_capabilities": capabilities.as_dict(),
        "chat_intent": intent,
        # Adapter capability *and* the checkout gate. The one case this still
        # cannot see is a turn that arrives without a run id for the bridge —
        # there is no turn yet — and that turn says so in its own reply.
        "chat_mode": mode.as_dict(),
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
    interactive: bool = False,
    advisory_reason: str = "",
) -> str:
    sections = [
        "# Loregarden branch triage",
        "You are Baxter, the operator's triage assistant for cleaning up git branches.",
    ]
    if interactive:
        sections.extend(
            [
                "You have real file, shell, git, and Loregarden MCP access in the selected "
                "branch's checkout.",
                "When the operator requests git work (commit, push, checkout, merge, rebase, "
                "delete, etc.), run it and report exact outcomes.",
                "Use safe defaults: avoid force-push or branch deletion unless the operator "
                "clearly asks; confirm when intent is ambiguous.",
                "High-risk actions route through Loregarden's approval inbox automatically.",
            ]
        )
    else:
        sections.append(
            "You are advisory only in this turn — do not claim to have run commands or changed "
            "the repository."
        )
        if advisory_reason:
            sections.append(f"Reason: {advisory_reason}")
    sections.extend(
        [
            "",
            f"Workspace: {workspace.name} ({workspace.slug})",
            f"Branch: {branch}",
        ]
    )

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
    session: Session,
    workspace: Workspace,
    branch: str,
    latest_user_message: str,
    *,
    run_id: str = "",
    turn_id: str = "",
) -> str:
    """Run one branch triage turn.

    ``turn_id`` is the pending assistant row this turn settles onto; passing it
    streams the agent's reasoning to that turn's thinking channel.
    """
    stub = stub_response(BRANCH_TRIAGE_CLI_PROFILE)
    if stub is not None:
        return stub

    ticket = _linked_ticket(session, workspace.id, branch)
    history = list_branch_triage_messages(session, workspace.id, branch)
    branch_entry = _branch_entry(session, workspace, branch)
    effective_workspace = apply_triage_runtime_overrides(workspace, ticket) if ticket else workspace
    agent = get_agent(TRIAGE_AGENT_ID) or {}
    selected = resolve_effective_adapter(
        agent_adapter=agent.get("adapter", "claude"),
        workspace=effective_workspace,
    )
    checkout_root = resolve_branch_checkout(workspace, branch)
    # One resolver decides the mode here and on the snapshot, so what the pill
    # promised and what this turn does cannot disagree.
    mode = resolve_chat_mode(
        selected,
        branch_checked_out=checkout_root is not None,
        has_run_for_approvals=bool(run_id),
    )
    intent: TurnIntent = "execute" if mode.can_act else "advisory"
    advisory_reason = f"{mode.reason} {mode.advice}".strip() if mode.reason else ""

    prompt = build_branch_triage_prompt(
        workspace,
        branch,
        branch_entry,
        history,
        latest_user_message,
        ticket=ticket,
        interactive=intent == "execute",
        advisory_reason=advisory_reason,
    )
    turn = run_agent_turn(
        AgentTurnRequest(
            session=session,
            workspace=effective_workspace,
            prompt=prompt,
            profile=BRANCH_TRIAGE_CLI_PROFILE,
            agent=agent,
            intent=intent,
            adapter=selected,
            user_prompt=os.environ.get(
                "LOREGARDEN_BRANCH_TRIAGE_USER_PROMPT", DEFAULT_BRANCH_TRIAGE_USER_PROMPT
            ),
            turn_id=turn_id,
            run_id=run_id,
            manage_run=False,
            workspace_root=checkout_root if intent == "execute" else None,
            workspace_stage_key=BRANCH_TRIAGE_STAGE_KEY,
            claude_model_env="LOREGARDEN_BRANCH_TRIAGE_CLAUDE_MODEL",
            track_workflow_stage=False,
        )
    )
    return turn.reply
