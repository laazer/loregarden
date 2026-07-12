"""Ticket Studio — agent-assisted feature scoping into work item hierarchies."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from loregarden.agents.cli_adapters import build_triage_invocation
from loregarden.agents.registry import get_agent
from loregarden.config import settings
from loregarden.models.domain import (
    VALID_HIERARCHY,
    Ticket,
    TicketStudioCommitResult,
    TicketStudioDraftItem,
    TicketStudioMessage,
    TicketStudioSession,
    TicketStudioSessionCreate,
    TicketStudioSessionStatus,
    TicketStudioSessionUpdate,
    TicketStudioSessionView,
    WorkItemType,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.services.cli_output import extract_triage_reply
from loregarden.services.cli_settings import VALID_CLI_ADAPTERS
from loregarden.services.hierarchy_service import validate_parent_child
from loregarden.services.ticket_service import TicketService
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

TICKET_STUDIO_AGENT_ID = "ticket_scoper"
MAX_STUDIO_HISTORY_MESSAGES = 16
MAX_STUDIO_MESSAGE_CHARS = 3000
MAX_STUDIO_BRIEF_CHARS = 8000

SCOPE_JSON_SCHEMA = """```json
{
  "summary": "One paragraph overview of the scoped feature",
  "clarifying_questions": ["Optional questions for the operator"],
  "tickets": [
    {
      "ref": "feature-1",
      "work_item_type": "feature",
      "parent_ref": null,
      "title": "Short title",
      "description": "Problem statement and scope",
      "acceptance_criteria": ["Testable criterion"],
      "priority": 2,
      "suggested_agent": "planner"
    },
    {
      "ref": "cap-1",
      "work_item_type": "capability",
      "parent_ref": "feature-1",
      "title": "Capability slice",
      "description": "What this slice delivers",
      "acceptance_criteria": ["Criterion"],
      "priority": 2,
      "suggested_agent": "spec"
    }
  ]
}
```"""


def resolve_ticket_studio_timeout(agent: dict) -> int:
    env = os.environ.get("LOREGARDEN_TICKET_STUDIO_TIMEOUT")
    if env:
        return max(30, int(env))
    return int(agent.get("timeout", settings.triage_timeout_seconds))


def get_studio_runtime(session_row: TicketStudioSession) -> WorkspaceRuntimeSettings:
    data = json.loads(session_row.runtime_json or "{}")
    return WorkspaceRuntimeSettings(
        cli_adapter=str(data.get("cli_adapter") or "default"),
        claude_model=str(data.get("claude_model") or ""),
        cursor_model=str(data.get("cursor_model") or ""),
        lmstudio_base_url=str(data.get("lmstudio_base_url") or ""),
        lmstudio_model=str(data.get("lmstudio_model") or ""),
    )


def apply_studio_runtime_overrides(
    workspace: Workspace, session_row: TicketStudioSession
) -> Workspace:
    overrides = json.loads(session_row.runtime_json or "{}")
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


def extract_json_block(text: str) -> dict | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if not raw:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            raw = text[start : end + 1]
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def parse_scope_payload(text: str) -> tuple[str, list[str], list[TicketStudioDraftItem]]:
    payload = extract_json_block(text)
    if not payload:
        return "", [], []

    summary = str(payload.get("summary") or "").strip()
    questions_raw = payload.get("clarifying_questions") or []
    questions = [str(item).strip() for item in questions_raw if str(item).strip()]

    items: list[TicketStudioDraftItem] = []
    for raw in payload.get("tickets") or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        try:
            work_item_type = WorkItemType(str(raw.get("work_item_type") or "task"))
        except ValueError:
            work_item_type = WorkItemType.TASK
        ac_raw = raw.get("acceptance_criteria") or []
        acceptance = [str(line).strip() for line in ac_raw if str(line).strip()]
        priority = int(raw.get("priority") or 3)
        if priority < 1 or priority > 3:
            priority = 3
        parent_ref = raw.get("parent_ref")
        items.append(
            TicketStudioDraftItem(
                ref=str(raw.get("ref") or f"item-{len(items) + 1}"),
                work_item_type=work_item_type,
                parent_ref=str(parent_ref) if parent_ref else None,
                title=title,
                description=str(raw.get("description") or "").strip(),
                acceptance_criteria=acceptance,
                priority=priority,
                suggested_agent=str(raw.get("suggested_agent") or "").strip(),
                selected=bool(raw.get("selected", True)),
            )
        )
    return summary, questions, items


def _load_clarifying_answers(session_row: TicketStudioSession) -> list[str]:
    data = json.loads(session_row.clarifying_answers_json or "[]")
    return [str(item) for item in data]


def clarifying_questions_resolved(questions: list[str], answers: list[str]) -> bool:
    if not questions:
        return True
    if len(answers) < len(questions):
        return False
    return all(str(answers[index] or "").strip() for index in range(len(questions)))


def format_studio_reply_for_display(content: str) -> str:
    payload = extract_json_block(content)
    prose = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", "", content, flags=re.DOTALL).strip()
    if not payload:
        return prose or content.strip()

    parts: list[str] = []
    if prose:
        parts.append(prose)

    summary = str(payload.get("summary") or "").strip()
    if summary and summary not in prose:
        parts.append(summary)

    questions = [
        str(item).strip()
        for item in (payload.get("clarifying_questions") or [])
        if str(item).strip()
    ]
    if questions:
        parts.append("**Questions**\n" + "\n".join(f"- {question}" for question in questions))

    tickets = payload.get("tickets") or []
    if tickets:
        parts.append(f"**Proposed {len(tickets)} draft ticket(s)** — see the draft panel.")

    if parts:
        return "\n\n".join(parts)
    return "Updated scope."


def _clarifications_block(questions: list[str], answers: list[str]) -> list[str]:
    if not questions:
        return []
    lines = ["## Operator clarifications"]
    for index, question in enumerate(questions):
        answer = answers[index].strip() if index < len(answers) else ""
        lines.append(f"Q: {question}")
        lines.append(f"A: {answer or '—'}")
    return lines


def _message_view(msg: TicketStudioMessage) -> dict:
    return {
        "id": msg.id,
        "role": msg.role,
        "content": msg.content,
        "display_content": format_studio_reply_for_display(msg.content)
        if msg.role == "assistant"
        else msg.content,
        "created_at": msg.created_at.isoformat(),
    }


def _load_draft(session_row: TicketStudioSession) -> list[TicketStudioDraftItem]:
    data = json.loads(session_row.draft_json or "[]")
    return [TicketStudioDraftItem.model_validate(item) for item in data]


def _session_view(
    session: Session,
    session_row: TicketStudioSession,
    *,
    messages: list[TicketStudioMessage] | None = None,
) -> TicketStudioSessionView:
    workspace = session.get(Workspace, session_row.workspace_id)
    parent_title = ""
    if session_row.parent_ticket_id:
        parent = session.get(Ticket, session_row.parent_ticket_id)
        if parent:
            parent_title = parent.title
    if messages is None:
        messages = list_studio_messages(session, session_row.id)
    questions = json.loads(session_row.clarifying_questions_json or "[]")
    answers = _load_clarifying_answers(session_row)
    imported_tickets = json.loads(session_row.imported_tickets_json or "[]")
    return TicketStudioSessionView(
        id=session_row.id,
        workspace_slug=workspace.slug if workspace else "",
        title=session_row.title,
        brief=session_row.brief,
        parent_ticket_id=session_row.parent_ticket_id,
        parent_ticket_title=parent_title,
        status=session_row.status,
        summary=session_row.summary,
        clarifying_questions=questions,
        clarifying_answers=answers,
        clarifying_resolved=clarifying_questions_resolved(questions, answers),
        draft=_load_draft(session_row),
        messages=[_message_view(msg) for msg in messages],
        runtime=get_studio_runtime(session_row).model_dump(),
        is_preview=session_row.is_preview,
        imported_tickets=imported_tickets,
        created_at=session_row.created_at,
        updated_at=session_row.updated_at,
    )


def list_studio_messages(
    session: Session, session_id: str, *, limit: int = 200
) -> list[TicketStudioMessage]:
    return list(
        session.exec(
            select(TicketStudioMessage)
            .where(TicketStudioMessage.session_id == session_id)
            .order_by(TicketStudioMessage.created_at.asc())
            .limit(limit)
        ).all()
    )


def build_studio_prompt(
    session_row: TicketStudioSession,
    workspace: Workspace,
    history: list[TicketStudioMessage],
    latest_user_message: str,
    *,
    session: Session,
    mode: str = "chat",
) -> str:
    parent_block: list[str] = []
    if session_row.parent_ticket_id:
        parent = session.get(Ticket, session_row.parent_ticket_id)
        if parent:
            parent_block = [
                f"Parent work item: {parent.external_id} · {parent.title} ({parent.work_item_type.value})",
                "Root scoped tickets must be valid children of this parent.",
                f"Allowed root types: {[t.value for t in VALID_HIERARCHY.get(parent.work_item_type, [])]}",
                "",
            ]

    brief = (session_row.brief or "—")[:MAX_STUDIO_BRIEF_CHARS]
    if session_row.brief and len(session_row.brief) > MAX_STUDIO_BRIEF_CHARS:
        brief += "…"

    current_draft = _load_draft(session_row)
    draft_lines = []
    for item in current_draft:
        draft_lines.append(
            f"- [{item.ref}] {item.work_item_type.value}: {item.title}"
            + (f" (parent: {item.parent_ref})" if item.parent_ref else "")
        )

    questions = json.loads(session_row.clarifying_questions_json or "[]")
    answers = _load_clarifying_answers(session_row)

    sections = [
        "# Loregarden Ticket Studio",
        "You are the Ticket Studio scoping assistant.",
        "Help the operator break a feature or initiative into a hierarchy of Loregarden work items.",
        "Respond in concise prose. When emitting structured scope, use a single fenced JSON block only — no markdown tables.",
        "",
        "Valid hierarchy:",
        "- milestone → feature | bug",
        "- feature → capability | bug",
        "- capability → task | bug",
        "",
        f"Workspace: {workspace.slug}",
        f"Session title: {session_row.title}",
        *parent_block,
        "## Feature brief",
        brief,
        "",
    ]

    if current_draft:
        sections.extend(["## Current draft tickets", *draft_lines, ""])

    clarification_lines = _clarifications_block(questions, answers)
    if clarification_lines:
        sections.extend([*clarification_lines, ""])

    if history:
        sections.append("## Conversation")
        for msg in history[-MAX_STUDIO_HISTORY_MESSAGES:]:
            speaker = "Operator" if msg.role == "user" else "Assistant"
            content = msg.content
            if len(content) > MAX_STUDIO_MESSAGE_CHARS:
                content = content[:MAX_STUDIO_MESSAGE_CHARS] + "…"
            sections.append(f"{speaker}: {content}")
        sections.append("")

    sections.extend(["## Latest operator message", latest_user_message, ""])

    if mode == "clarify":
        sections.extend(
            [
                "## Task",
                "Review the feature brief and identify ambiguities before ticket generation.",
                "Output JSON with `summary`, `clarifying_questions`, and `tickets: []`.",
                "Do not propose tickets yet.",
                "If the brief is already clear, return an empty `clarifying_questions` array.",
                SCOPE_JSON_SCHEMA,
            ]
        )
    elif mode == "scope":
        sections.extend(
            [
                "## Task",
                "Produce the full ticket breakdown using operator clarifications when provided.",
                "Output JSON with `summary`, `clarifying_questions: []`, and populated `tickets`.",
                "Do not ask new clarifying questions — use reasonable defaults for anything still ambiguous.",
                SCOPE_JSON_SCHEMA,
                "",
                "Rules:",
                "- Size the hierarchy to the brief: a small, self-contained change should be a single",
                "  task or bug ticket. Do not add feature/capability layers just to fill the hierarchy.",
                "- Only use feature → capabilities → tasks for work that genuinely has multiple",
                "  independent slices; scoped-under-an-existing-parent work should stay as flat as the",
                "  parent's hierarchy allows.",
                "- Use unique `ref` values and `parent_ref` pointers (null for roots).",
                "- Each ticket needs testable acceptance_criteria.",
                "- Keep tasks small enough for one agent run.",
            ]
        )
    else:
        sections.extend(
            [
                "## Task",
                "Reply conversationally in 2–6 sentences to refine the scope.",
                "If the operator asks to generate tickets, remind them to answer clarifying questions first,",
                "then use Generate tickets. Only include JSON when explicitly asked to draft tickets.",
            ]
        )

    return "\n".join(sections)


def invoke_ticket_studio_model(
    session: Session,
    session_row: TicketStudioSession,
    latest_user_message: str,
    *,
    mode: str = "chat",
) -> str:
    stub = os.environ.get("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE")
    if stub is not None:
        return stub

    workspace = session.get(Workspace, session_row.workspace_id)
    if not workspace:
        raise ValueError("Session workspace not found")

    effective_workspace = apply_studio_runtime_overrides(workspace, session_row)
    repo_root = resolve_workspace_root(effective_workspace)
    if not repo_root.is_dir():
        raise ValueError(f"Workspace repo path does not exist: {repo_root}")

    agent = get_agent(TICKET_STUDIO_AGENT_ID)
    if not agent:
        raise ValueError(f"Unknown ticket studio agent: {TICKET_STUDIO_AGENT_ID}")

    history = list_studio_messages(session, session_row.id)
    prompt = build_studio_prompt(
        session_row,
        workspace,
        history,
        latest_user_message,
        session=session,
        mode=mode,
    )

    with tempfile.TemporaryDirectory(prefix="loregarden-ticket-studio-") as tmp:
        prompt_file = Path(tmp) / "prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        invocation = build_triage_invocation(
            agent_id=TICKET_STUDIO_AGENT_ID,
            adapter=agent.get("adapter", "claude"),
            prompt=prompt,
            prompt_file=prompt_file,
            skill_name="",
            workspace_root=repo_root,
            workspace=effective_workspace,
        )
        timeout = resolve_ticket_studio_timeout(agent)
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
            raise TimeoutError(f"Ticket studio assistant timed out after {timeout}s") from None

        if proc.returncode != 0:
            detail = (
                stderr.decode("utf-8", errors="replace").strip()
                or stdout.decode("utf-8", errors="replace").strip()
            )
            raise RuntimeError(detail or f"Ticket studio CLI exited with code {proc.returncode}")

        reply = extract_triage_reply(stdout.decode("utf-8", errors="replace"))
        if not reply:
            raise RuntimeError("Ticket studio assistant returned an empty response")
        # "clarify"/"scope" replies carry a JSON block whose size scales with ticket count;
        # truncating those mid-JSON breaks parsing, so only guard against runaway output.
        cap = 12000 if mode == "chat" else 200_000
        return reply[:cap]


def _ensure_root_milestone(
    session_row: TicketStudioSession, items: list[TicketStudioDraftItem]
) -> list[TicketStudioDraftItem]:
    """Surface the parentless-root milestone requirement in the draft itself.

    TicketService.create_ticket rejects a parentless feature/capability/task/bug
    (only milestones may be root). When the session has no parent ticket and the
    model proposed a non-milestone root, add an explicit milestone draft item so
    the operator sees and can edit it, rather than one appearing invisibly at commit.
    """
    if session_row.parent_ticket_id:
        return items
    roots = [item for item in items if not item.parent_ref]
    non_milestone_roots = [item for item in roots if item.work_item_type != WorkItemType.MILESTONE]
    if not non_milestone_roots:
        return items

    existing_refs = {item.ref for item in items}
    milestone_ref = "milestone-root"
    while milestone_ref in existing_refs:
        milestone_ref += "-1"

    milestone_item = TicketStudioDraftItem(
        ref=milestone_ref,
        work_item_type=WorkItemType.MILESTONE,
        parent_ref=None,
        title=session_row.title or "Untitled scope",
        description=session_row.brief,
    )
    for item in non_milestone_roots:
        item.parent_ref = milestone_ref
    return [milestone_item, *items]


def _apply_scope_to_session(
    session_row: TicketStudioSession,
    reply: str,
    *,
    apply_tickets: bool = True,
) -> bool:
    summary, questions, items = parse_scope_payload(reply)
    if not items and not summary and not questions:
        return False
    if summary:
        session_row.summary = summary
    if questions:
        session_row.clarifying_questions_json = json.dumps(questions)
        existing = _load_clarifying_answers(session_row)
        session_row.clarifying_answers_json = json.dumps(
            [existing[index] if index < len(existing) else "" for index in range(len(questions))]
        )
    if items and apply_tickets:
        items = _ensure_root_milestone(session_row, items)
        session_row.draft_json = json.dumps([item.model_dump(mode="json") for item in items])
        session_row.clarifying_questions_json = "[]"
        session_row.clarifying_answers_json = "[]"
    return bool(items or summary or questions)


def _validate_draft_hierarchy(
    items: list[TicketStudioDraftItem],
    *,
    parent_ticket: Ticket | None,
) -> None:
    refs = {item.ref for item in items}
    for item in items:
        if item.parent_ref and item.parent_ref not in refs:
            raise ValueError(
                f"Draft item {item.ref} references unknown parent_ref: {item.parent_ref}"
            )

    roots = [item for item in items if not item.parent_ref]
    if parent_ticket:
        for root in roots:
            validate_parent_child(parent_ticket.work_item_type, root.work_item_type)
    else:
        for root in roots:
            if root.work_item_type not in {WorkItemType.MILESTONE, WorkItemType.FEATURE}:
                raise ValueError(
                    f"Root item {root.ref} must be milestone or feature when no parent is set"
                )

    for item in items:
        if not item.parent_ref:
            continue
        parent_item = next((p for p in items if p.ref == item.parent_ref), None)
        if not parent_item:
            continue
        validate_parent_child(parent_item.work_item_type, item.work_item_type)


def _topo_sort_items(items: list[TicketStudioDraftItem]) -> list[TicketStudioDraftItem]:
    by_ref = {item.ref: item for item in items}
    ordered: list[TicketStudioDraftItem] = []
    seen: set[str] = set()

    def visit(ref: str) -> None:
        if ref in seen:
            return
        item = by_ref.get(ref)
        if not item:
            return
        if item.parent_ref:
            visit(item.parent_ref)
        seen.add(ref)
        ordered.append(item)

    for item in items:
        visit(item.ref)
    return ordered


class TicketStudioService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_sessions(self, *, workspace_slug: str | None = None) -> list[TicketStudioSessionView]:
        query = select(TicketStudioSession).order_by(TicketStudioSession.updated_at.desc())
        if workspace_slug:
            ws = self.session.exec(
                select(Workspace).where(Workspace.slug == workspace_slug)
            ).first()
            if not ws:
                return []
            query = query.where(TicketStudioSession.workspace_id == ws.id)
        rows = self.session.exec(query).all()
        return [_session_view(self.session, row) for row in rows]

    def get_session(self, session_id: str) -> TicketStudioSessionView | None:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            return None
        return _session_view(self.session, row)

    def create_session(self, body: TicketStudioSessionCreate) -> TicketStudioSessionView:
        title = body.title.strip()
        if not title:
            raise ValueError("Title is required")

        ws = self.session.exec(
            select(Workspace).where(Workspace.slug == body.workspace_slug)
        ).first()
        if not ws:
            raise ValueError(f"Workspace not found: {body.workspace_slug}")

        parent: Ticket | None = None
        if body.parent_ticket_id:
            parent = self.session.get(Ticket, body.parent_ticket_id)
            if not parent or parent.workspace_id != ws.id:
                raise ValueError("Parent ticket not found in workspace")

        now = datetime.now(timezone.utc)
        imported_tickets_json = json.dumps(body.imported_tickets) if body.imported_tickets else "[]"
        is_preview = body.is_preview or bool(body.imported_tickets)

        draft_items = []
        if body.imported_tickets:
            for idx, ticket in enumerate(body.imported_tickets):
                draft_items.append(
                    TicketStudioDraftItem(
                        ref=ticket.get("external_id", f"imported-{idx}"),
                        work_item_type=ticket.get("work_item_type", WorkItemType.TASK),
                        parent_ref=ticket.get("parent_external_id"),
                        title=ticket.get("title", "Imported ticket"),
                        description=ticket.get("description", ""),
                        acceptance_criteria=ticket.get("acceptance_criteria", []),
                        priority=ticket.get("priority", 3),
                        selected=True,
                    )
                )

        row = TicketStudioSession(
            workspace_id=ws.id,
            title=title,
            brief=body.brief.strip(),
            parent_ticket_id=body.parent_ticket_id,
            is_preview=is_preview,
            imported_tickets_json=imported_tickets_json,
            draft_json=json.dumps([item.model_dump(mode="json") for item in draft_items])
            if draft_items
            else "[]",
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _session_view(self.session, row)

    def update_session(
        self, session_id: str, body: TicketStudioSessionUpdate
    ) -> TicketStudioSessionView:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Only draft sessions can be edited")

        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise ValueError("Title is required")
            row.title = title
        if body.brief is not None:
            row.brief = body.brief.strip()
        if body.parent_ticket_id is not None:
            if body.parent_ticket_id:
                ws = self.session.get(Workspace, row.workspace_id)
                parent = self.session.get(Ticket, body.parent_ticket_id)
                if not parent or not ws or parent.workspace_id != ws.id:
                    raise ValueError("Parent ticket not found in workspace")
            row.parent_ticket_id = body.parent_ticket_id or None

        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _session_view(self.session, row)

    def delete_session(self, session_id: str) -> None:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        messages = self.session.exec(
            select(TicketStudioMessage).where(TicketStudioMessage.session_id == session_id)
        ).all()
        for msg in messages:
            self.session.delete(msg)
        self.session.delete(row)
        self.session.commit()

    def set_runtime(
        self, session_id: str, body: WorkspaceRuntimeUpdate
    ) -> WorkspaceRuntimeSettings:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if body.cli_adapter not in VALID_CLI_ADAPTERS:
            raise ValueError(f"Invalid cli_adapter: {body.cli_adapter}")
        payload = {
            "cli_adapter": body.cli_adapter,
            "claude_model": body.claude_model.strip(),
            "cursor_model": body.cursor_model.strip(),
            "lmstudio_base_url": body.lmstudio_base_url.strip(),
            "lmstudio_model": body.lmstudio_model.strip(),
        }
        row.runtime_json = json.dumps(payload)
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return get_studio_runtime(row)

    def update_draft(
        self, session_id: str, items: list[TicketStudioDraftItem]
    ) -> TicketStudioSessionView:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Only draft sessions can be edited")

        parent = self.session.get(Ticket, row.parent_ticket_id) if row.parent_ticket_id else None
        _validate_draft_hierarchy(items, parent_ticket=parent)

        row.draft_json = json.dumps([item.model_dump(mode="json") for item in items])
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _session_view(self.session, row)

    def send_message(self, session_id: str, content: str) -> TicketStudioSessionView:
        text = content.strip()
        if not text:
            raise ValueError("Message cannot be empty")

        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Cannot chat on a committed session")

        user_message = TicketStudioMessage(session_id=row.id, role="user", content=text)
        self.session.add(user_message)
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(user_message)

        try:
            reply = invoke_ticket_studio_model(self.session, row, text, mode="chat")
        except Exception as exc:
            reply = f"Ticket studio assistant unavailable: {exc}"

        assistant_message = TicketStudioMessage(session_id=row.id, role="assistant", content=reply)
        self.session.add(assistant_message)
        _apply_scope_to_session(row, reply, apply_tickets=False)
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()

        messages = list_studio_messages(self.session, row.id)
        self.session.refresh(row)
        return _session_view(self.session, row, messages=messages)

    def request_clarifications(self, session_id: str) -> TicketStudioSessionView:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Cannot clarify a committed session")

        prompt = "Review this feature brief and return clarifying questions only (no tickets yet)."
        user_message = TicketStudioMessage(session_id=row.id, role="user", content=prompt)
        self.session.add(user_message)
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()

        try:
            reply = invoke_ticket_studio_model(self.session, row, prompt, mode="clarify")
        except Exception as exc:
            reply = f"Ticket studio assistant unavailable: {exc}"

        assistant_message = TicketStudioMessage(session_id=row.id, role="assistant", content=reply)
        self.session.add(assistant_message)
        _apply_scope_to_session(row, reply, apply_tickets=False)
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _session_view(self.session, row)

    def save_clarifications(self, session_id: str, answers: list[str]) -> TicketStudioSessionView:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Only draft sessions can be edited")

        questions = json.loads(row.clarifying_questions_json or "[]")
        if not questions:
            raise ValueError("No clarifying questions to answer")

        normalized = [
            str(answers[index] if index < len(answers) else "").strip()
            for index in range(len(questions))
        ]
        if not clarifying_questions_resolved(questions, normalized):
            raise ValueError("Answer every clarifying question before generating tickets")

        row.clarifying_answers_json = json.dumps(normalized)
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _session_view(self.session, row)

    def generate_scope(self, session_id: str) -> TicketStudioSessionView:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Cannot scope a committed session")

        questions = json.loads(row.clarifying_questions_json or "[]")
        answers = _load_clarifying_answers(session_row=row)
        if questions and not clarifying_questions_resolved(questions, answers):
            raise ValueError("Answer all clarifying questions before generating tickets")

        prompt = "Generate the full ticket breakdown for this feature. Output tickets in the JSON scope block."
        user_message = TicketStudioMessage(session_id=row.id, role="user", content=prompt)
        self.session.add(user_message)
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()

        try:
            reply = invoke_ticket_studio_model(self.session, row, prompt, mode="scope")
        except Exception as exc:
            reply = f"Ticket studio assistant unavailable: {exc}"

        assistant_message = TicketStudioMessage(session_id=row.id, role="assistant", content=reply)
        self.session.add(assistant_message)
        if not _apply_scope_to_session(row, reply, apply_tickets=True):
            row.summary = (
                row.summary
                or "Scope generation did not return structured tickets — refine the brief or chat further."
            )
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _session_view(self.session, row)

    def commit_session(self, session_id: str) -> TicketStudioCommitResult:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Session already committed")

        workspace = self.session.get(Workspace, row.workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")

        draft = [item for item in _load_draft(row) if item.selected]
        if not draft:
            raise ValueError("No selected tickets in draft")

        parent_ticket = (
            self.session.get(Ticket, row.parent_ticket_id) if row.parent_ticket_id else None
        )
        _validate_draft_hierarchy(draft, parent_ticket=parent_ticket)

        ticket_svc = TicketService(self.session)
        ref_to_id: dict[str, str] = {}
        created_ids: list[str] = []
        breakdown: dict[str, int] = {}
        synthetic_milestone_id: str | None = None
        root_ticket_id: str | None = row.parent_ticket_id

        for item in _topo_sort_items(draft):
            parent_id: str | None
            if item.parent_ref:
                parent_id = ref_to_id.get(item.parent_ref)
                if not parent_id:
                    raise ValueError(f"Unresolved parent_ref for {item.ref}")
            elif row.parent_ticket_id:
                parent_id = row.parent_ticket_id
            elif item.work_item_type == WorkItemType.MILESTONE:
                parent_id = None
            else:
                # Root item has no session parent and isn't itself a milestone —
                # synthesize one so it has a legal parent (only milestones may be parentless).
                if not synthetic_milestone_id:
                    milestone = ticket_svc.create_ticket(
                        workspace_slug=workspace.slug,
                        title=row.title or "Untitled scope",
                        work_item_type=WorkItemType.MILESTONE,
                        description=row.brief,
                    )
                    synthetic_milestone_id = milestone.id
                    created_ids.append(milestone.id)
                    breakdown[WorkItemType.MILESTONE.value] = (
                        breakdown.get(WorkItemType.MILESTONE.value, 0) + 1
                    )
                    if root_ticket_id is None:
                        root_ticket_id = synthetic_milestone_id
                parent_id = synthetic_milestone_id

            created = ticket_svc.create_ticket(
                workspace_slug=workspace.slug,
                title=item.title,
                work_item_type=item.work_item_type,
                parent_ticket_id=parent_id,
                description=item.description,
                acceptance_criteria=item.acceptance_criteria,
                priority=item.priority,
            )
            ref_to_id[item.ref] = created.id
            created_ids.append(created.id)
            breakdown[item.work_item_type.value] = breakdown.get(item.work_item_type.value, 0) + 1
            if root_ticket_id is None and parent_id is None:
                root_ticket_id = created.id

        row.status = TicketStudioSessionStatus.COMMITTED
        row.is_preview = False
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()

        return TicketStudioCommitResult(
            session_id=row.id,
            created_ticket_ids=created_ids,
            created_count=len(created_ids),
            breakdown=breakdown,
            root_ticket_id=root_ticket_id,
        )
