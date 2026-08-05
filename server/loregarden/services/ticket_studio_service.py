"""Ticket Studio — agent-assisted feature scoping into work item hierarchies."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from loregarden.agents.cli_adapters import resolve_effective_adapter
from loregarden.agents.registry import get_agent
from loregarden.models.domain import (
    VALID_HIERARCHY,
    ReferenceRepo,
    Ticket,
    TicketStudioCommitResult,
    TicketStudioDraftItem,
    TicketStudioMessage,
    TicketStudioSession,
    TicketStudioSessionCreate,
    TicketStudioSessionStatus,
    TicketStudioSessionUpdate,
    TicketStudioSessionView,
    TicketStudioSurveyFinding,
    WorkItemType,
    Workspace,
    WorkspaceRuntimeSettings,
    WorkspaceRuntimeUpdate,
)
from loregarden.services.chat_primitives import load_parts_json, parts_json_for_reply
from loregarden.services.chat_thinking import ChatTurnThinkingSink
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    run_cli_agent_turn,
    stub_response,
)
from loregarden.services.cli_settings import (
    VALID_CLI_ADAPTERS,
    apply_runtime_overrides,
    parse_runtime_settings,
)
from loregarden.services.draft_hierarchy import (
    DraftHierarchyError,
    find_hierarchy_violations,
    repair_draft_hierarchy,
    topo_sort_draft_items,
)
from loregarden.services.integration_review import ensure_reviews_for_tickets
from loregarden.services.reference_repo_service import ReferenceRepoService, is_cloned
from loregarden.services.ticket_service import TicketService
from loregarden.services.workflow_service import WorkflowService
from sqlmodel import Session, col, select

TICKET_STUDIO_AGENT_ID = "ticket_scoper"

# Which scoper turn a pending row is, recorded on the row itself. The mode picks
# the prompt AND how the reply is applied to the session, so a worker that did
# not start the turn can still finish it exactly as the starter intended.
STUDIO_TURN_CHAT = "chat"
STUDIO_TURN_CLARIFY = "clarify"
# A clarify turn that generates the breakdown itself when nothing is unclear —
# how a brand-new session bootstraps without stranding the operator on an empty draft.
STUDIO_TURN_BOOTSTRAP_CLARIFY = "bootstrap_clarify"
STUDIO_TURN_SCOPE = "scope"

# The prompt (and reply cap) each turn mode runs with.
STUDIO_PROMPT_MODES = {
    STUDIO_TURN_CHAT: "chat",
    STUDIO_TURN_CLARIFY: "clarify",
    STUDIO_TURN_BOOTSTRAP_CLARIFY: "clarify",
    STUDIO_TURN_SCOPE: "scope",
}

MAX_STUDIO_HISTORY_MESSAGES = 16
MAX_STUDIO_MESSAGE_CHARS = 3000
MAX_STUDIO_BRIEF_CHARS = 8000
SCOPE_REPLY_CAP = 200_000

TICKET_STUDIO_CLI_PROFILE = CliAgentProfile(
    agent_id=TICKET_STUDIO_AGENT_ID,
    assistant_label="Ticket studio assistant",
    cli_label="Ticket studio",
    stub_env="LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE",
    timeout_env="LOREGARDEN_TICKET_STUDIO_TIMEOUT",
    tmp_prefix="loregarden-ticket-studio-",
    reply_cap=12000,
)

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
      "priority": 2
    },
    {
      "ref": "cap-1",
      "work_item_type": "capability",
      "parent_ref": "feature-1",
      "title": "Capability slice",
      "description": "What this slice delivers",
      "acceptance_criteria": ["Criterion"],
      "priority": 2
    }
  ]
}
```"""


SURVEY_JSON_SCHEMA = """```json
{
  "summary": "What this reference repo is and how much of it is relevant here",
  "clarifying_questions": [],
  "findings": [
    {
      "ref": "find-1",
      "title": "Short name for the part",
      "repo_slug": "github.com/owner/repo",
      "source_paths": ["path/in/reference/repo", "another/path"],
      "what_it_gives": "The capability we would gain, in our terms",
      "fit": "How it lines up with our stack and where it would live here",
      "risks": "Licensing, dependencies, conflicts with what we already have",
      "verdict": "adopt | adapt | inspire | skip",
      "effort": "S | M | L"
    }
  ]
}
```"""

VALID_SURVEY_VERDICTS = ("adopt", "adapt", "inspire", "skip")


def get_studio_runtime(session_row: TicketStudioSession) -> WorkspaceRuntimeSettings:
    return parse_runtime_settings(session_row.runtime_json)


def apply_studio_runtime_overrides(
    workspace: Workspace, session_row: TicketStudioSession
) -> Workspace:
    return apply_runtime_overrides(workspace, session_row.runtime_json)


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
                selected=bool(raw.get("selected", True)),
            )
        )
    return summary, questions, items


def parse_survey_payload(text: str) -> tuple[str, list[str], list[TicketStudioSurveyFinding]]:
    payload = extract_json_block(text)
    if not payload:
        return "", [], []

    summary = str(payload.get("summary") or "").strip()
    questions = [
        str(item).strip()
        for item in (payload.get("clarifying_questions") or [])
        if str(item).strip()
    ]

    findings: list[TicketStudioSurveyFinding] = []
    for raw in payload.get("findings") or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        verdict = str(raw.get("verdict") or "adapt").strip().lower()
        if verdict not in VALID_SURVEY_VERDICTS:
            verdict = "adapt"
        paths = [str(path).strip() for path in (raw.get("source_paths") or []) if str(path).strip()]
        findings.append(
            TicketStudioSurveyFinding(
                ref=str(raw.get("ref") or f"find-{len(findings) + 1}"),
                title=title,
                repo_slug=str(raw.get("repo_slug") or "").strip(),
                source_paths=paths,
                what_it_gives=str(raw.get("what_it_gives") or "").strip(),
                fit=str(raw.get("fit") or "").strip(),
                risks=str(raw.get("risks") or "").strip(),
                verdict=verdict,
                effort=str(raw.get("effort") or "").strip(),
                # Pre-check everything the scoper did not dismiss: the operator
                # prunes a shortlist rather than re-selecting the whole survey.
                selected=verdict != "skip",
            )
        )
    return summary, questions, findings


def _load_survey(session_row: TicketStudioSession) -> list[TicketStudioSurveyFinding]:
    data = json.loads(session_row.survey_json or "[]")
    return [TicketStudioSurveyFinding.model_validate(item) for item in data]


def _load_reference_repo_ids(session_row: TicketStudioSession) -> list[str]:
    data = json.loads(session_row.reference_repo_ids_json or "[]")
    return [str(item) for item in data]


def _reference_repo_block(session: Session, session_row: TicketStudioSession) -> list[str]:
    repos = ReferenceRepoService(session).get_many(_load_reference_repo_ids(session_row))
    available = [repo for repo in repos if repo.local_path and is_cloned(repo.local_path)]
    if not available:
        return []
    lines = [
        "## Reference repos",
        "Read-only checkouts of other projects, outside this repo. Read and grep them "
        "freely; never edit them, and never copy a file across without saying so in the "
        "scope.",
    ]
    for repo in available:
        lines.append(f"- {repo.slug} ({repo.url}) at `{repo.local_path}`")
        if repo.notes:
            lines.append(f"  Operator note: {repo.notes}")
    return lines


def _selected_findings_block(session_row: TicketStudioSession) -> list[str]:
    selected = [finding for finding in _load_survey(session_row) if finding.selected]
    if not selected:
        return []
    lines = [
        "## Reference parts the operator selected",
        "These are the surveyed parts to build tickets around. Anything not listed here "
        "was reviewed and dropped — do not scope it.",
    ]
    for finding in selected:
        paths = ", ".join(finding.source_paths) or "—"
        lines.append(
            f"- [{finding.ref}] {finding.title} ({finding.verdict}, effort {finding.effort or '?'})"
        )
        lines.append(f"  Source: {finding.repo_slug or '—'} :: {paths}")
        if finding.what_it_gives:
            lines.append(f"  Value: {finding.what_it_gives}")
        if finding.fit:
            lines.append(f"  Fit: {finding.fit}")
        if finding.risks:
            lines.append(f"  Risks: {finding.risks}")
    return lines


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


def _format_answers_message(questions: list[str], answers: list[str]) -> str:
    lines = ["Answers to your clarifying questions:"]
    for index, question in enumerate(questions):
        lines.append(f"- {question}")
        lines.append(f"  {answers[index] if index < len(answers) else ''}")
    return "\n".join(lines)


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
        "parts": load_parts_json(msg.parts_json),
        "created_at": msg.created_at.isoformat(),
    }


def _assistant_message(
    session: Session, session_row: TicketStudioSession, reply: str
) -> TicketStudioMessage:
    """An assistant turn with its parts resolved at write time.

    Every scoper path builds its assistant row through here so none of them can
    forget the parts and leave that turn rendering as raw fenced JSON after a
    reload.
    """
    return TicketStudioMessage(
        session_id=session_row.id,
        role="assistant",
        content=reply,
        parts_json=parts_json_for_reply(session, reply, workspace_id=session_row.workspace_id),
    )


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
    run_status, active_turn_id = studio_run_status(session, session_row.id)
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
        run_status=run_status,
        active_turn_id=active_turn_id,
        reference_repos=ReferenceRepoService(session).views_for(
            _load_reference_repo_ids(session_row)
        ),
        survey=_load_survey(session_row),
        created_at=session_row.created_at,
        updated_at=session_row.updated_at,
    )


def list_studio_messages(
    session: Session, session_id: str, *, limit: int = 200
) -> list[TicketStudioMessage]:
    """Settled messages only. A pending assistant row has no content yet — it is a
    turn in flight, surfaced through ``studio_run_status`` instead. Prompt history
    reads through here too, so an in-flight turn never feeds itself back.
    """
    return list(
        session.exec(
            select(TicketStudioMessage)
            .where(
                TicketStudioMessage.session_id == session_id,
                TicketStudioMessage.status != "pending",
            )
            .order_by(TicketStudioMessage.created_at.asc())
            .limit(limit)
        ).all()
    )


def latest_pending_studio_turn(session: Session, session_id: str) -> TicketStudioMessage | None:
    """The session's in-flight turn, if any."""
    return session.exec(
        select(TicketStudioMessage)
        .where(
            TicketStudioMessage.session_id == session_id,
            TicketStudioMessage.status == "pending",
        )
        .order_by(col(TicketStudioMessage.created_at).desc())
        .limit(1)
    ).first()


def studio_run_status(session: Session, session_id: str) -> tuple[str, str | None]:
    """Return (run_status, active_turn_id) for the session's latest scoper turn."""
    pending = latest_pending_studio_turn(session, session_id)
    if pending:
        return "running", pending.id
    return "idle", None


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
        "Valid hierarchy — the only parent each type may have:",
        "- milestone → feature | bug",
        "- feature → capability | bug",
        "- capability → task | bug",
        "",
        "Never skip a layer. A task's parent must be a capability, a capability's parent must",
        "be a feature, and a feature's parent must be a milestone. A task hanging directly off",
        "a feature or milestone is rejected, as is any ticket parented to a task or a bug.",
        "",
        f"Workspace: {workspace.slug}",
        f"Session title: {session_row.title}",
        *parent_block,
        "## Feature brief",
        brief,
        "",
    ]

    reference_lines = _reference_repo_block(session, session_row)
    if reference_lines:
        sections.extend([*reference_lines, ""])

    if mode != "survey":
        findings_lines = _selected_findings_block(session_row)
        if findings_lines:
            sections.extend([*findings_lines, ""])

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

    if mode == "survey":
        sections.extend(
            [
                "## Task",
                "Survey the reference repos above against this repo and report which parts "
                "are worth bringing over for the brief. This is the only job this turn — "
                "do not propose tickets.",
                "Work from the actual source: read and grep both the reference repo and "
                "this repo before judging anything. A finding that does not name real "
                "paths in the reference repo is worthless.",
                "For each candidate part, decide honestly between:",
                "- adopt: port it largely as-is, it fits our stack",
                "- adapt: the idea and much of the code carry over, but it needs reshaping "
                "to our models/services",
                "- inspire: worth copying the approach, not the code",
                "- skip: looked at it, not worth it here (say why — a skipped finding saves "
                "the next person the same look)",
                "Reject parts we already have. Check this repo first and say so in `fit` "
                "when something duplicates existing code.",
                "Order findings by value to this project, highest first. Ten sharp findings "
                "beat forty shallow ones.",
                "Output JSON with `summary`, `clarifying_questions` (usually empty), and "
                "`findings`.",
                SURVEY_JSON_SCHEMA,
            ]
        )
    elif mode == "clarify":
        sections.extend(
            [
                "## Task",
                "Review the feature brief and identify ambiguities before ticket generation.",
                "You have real Read/Grep/Bash access to this repo — investigate existing code, "
                "similar features, and config defaults first, and answer what you can from that "
                "instead of asking. Only list a question when it is a genuine product/design call "
                "the operator must make and would materially change the ticket hierarchy.",
                "Output JSON with `summary`, `clarifying_questions`, and `tickets: []`.",
                "Do not propose tickets yet.",
                "If the brief is already clear (or answerable from the codebase), return an empty "
                "`clarifying_questions` array.",
                "The operator's answers may appear above. Re-ask only what they genuinely left "
                "unresolved. Once nothing material is outstanding, return an empty "
                "`clarifying_questions` array and use `summary` to say you have what you need to "
                "generate tickets.",
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
                "  task or bug ticket rather than a deep tree, and work scoped under an existing parent",
                "  should start as close to that parent as the hierarchy allows.",
                "- Keeping it small means fewer siblings, never a skipped layer. Do not invent extra",
                "  capabilities to fill out the tree — but every task you emit still needs a capability",
                "  parent, and every capability a feature parent, up to the root.",
                "- Use unique `ref` values and `parent_ref` pointers (null for roots).",
                "- Each ticket needs testable acceptance_criteria.",
                "- Keep tasks small enough for one agent run.",
                "- When a ticket comes from a selected reference part, name the reference "
                "  repo and the exact source paths in its description, and say whether the "
                "  implementer should port, adapt, or merely take the approach. The "
                "  implementer cannot see this survey.",
            ]
        )
    else:
        sections.extend(
            [
                "## Task",
                "Reply conversationally in 2–6 sentences to refine the scope.",
                "You have real Read/Grep/Bash access to this repo — before asking the operator "
                "anything, check whether the codebase already answers it (existing similar "
                "features, models/services, naming conventions, config defaults).",
                "You've already asked one round of clarifying questions in this conversation if "
                "any appear above — do not open a new round of questions. Instead, state your best "
                "reasonable assumption for anything still unresolved and move toward scope, "
                "inviting a correction rather than blocking on an answer.",
                "If the operator asks to generate tickets, remind them to answer clarifying questions first,",
                "then use Generate tickets. Only include JSON when explicitly asked to draft tickets.",
            ]
        )

    return "\n".join(sections)


def _readable_reference_dirs(session: Session, session_row: TicketStudioSession) -> list[str]:
    repos: list[ReferenceRepo] = ReferenceRepoService(session).get_many(
        _load_reference_repo_ids(session_row)
    )
    return [repo.local_path for repo in repos if repo.local_path and is_cloned(repo.local_path)]


def _assert_adapter_can_read_extra_dirs(workspace: Workspace) -> None:
    """Only the claude adapter takes --add-dir.

    Without this the survey still runs, reads nothing outside the workspace, and
    reports confident findings invented from the repo name alone.
    """
    agent = get_agent(TICKET_STUDIO_AGENT_ID) or {}
    adapter = resolve_effective_adapter(
        agent_adapter=agent.get("adapter", "claude"), workspace=workspace
    )
    if adapter != "claude":
        raise ValueError(
            f"Reference repos need the claude CLI adapter to be readable (this session "
            f"runs '{adapter}'). Switch the session runtime to claude, or detach the "
            f"reference repos."
        )


def invoke_ticket_studio_model(
    session: Session,
    session_row: TicketStudioSession,
    latest_user_message: str,
    *,
    mode: str = "chat",
    turn_id: str = "",
) -> str:
    """Run one scoper turn.

    ``turn_id`` is the pending assistant row this turn settles onto. Passing it
    streams the scoper's reasoning to that turn's thinking channel — a scope
    turn returns a whole ticket hierarchy and is the longest wait in the app,
    so it is the surface with the most to gain from not being a blank spinner.
    """
    stub = stub_response(TICKET_STUDIO_CLI_PROFILE)
    if stub is not None:
        return stub

    workspace = session.get(Workspace, session_row.workspace_id)
    if not workspace:
        raise ValueError("Session workspace not found")

    effective_workspace = apply_studio_runtime_overrides(workspace, session_row)
    extra_dirs = _readable_reference_dirs(session, session_row)
    if extra_dirs:
        _assert_adapter_can_read_extra_dirs(effective_workspace)

    history = list_studio_messages(session, session_row.id)
    prompt = build_studio_prompt(
        session_row,
        workspace,
        history,
        latest_user_message,
        session=session,
        mode=mode,
    )
    thinking = ChatTurnThinkingSink(turn_id) if turn_id else None
    try:
        return run_cli_agent_turn(
            TICKET_STUDIO_CLI_PROFILE,
            workspace=effective_workspace,
            prompt=prompt,
            # "clarify"/"scope"/"survey" replies carry a JSON block whose size scales with
            # ticket count; truncating those mid-JSON breaks parsing, so only guard against
            # runaway output.
            reply_cap=None if mode == "chat" else SCOPE_REPLY_CAP,
            extra_dirs=extra_dirs,
            thinking_sink=thinking,
        )
    finally:
        if thinking:
            thinking.close()


def _repair_draft_for_session(
    session_row: TicketStudioSession,
    items: list[TicketStudioDraftItem],
    *,
    parent_type: WorkItemType | None,
) -> list[TicketStudioDraftItem]:
    """Make a proposed draft legal before it reaches the session."""
    return repair_draft_hierarchy(
        items,
        parent_type=parent_type,
        root_title=session_row.title or "Untitled scope",
        root_description=session_row.brief,
    )


def _carry_over_answers(session_row: TicketStudioSession, questions: list[str]) -> list[str]:
    """Answers for a new round of questions, keeping only the ones still being asked.

    Answers used to be carried over by position, so a fresh round arrived pre-filled with
    the previous round's answers to entirely different questions.
    """
    previous_questions = json.loads(session_row.clarifying_questions_json or "[]")
    previous_answers = _load_clarifying_answers(session_row)
    answers: list[str] = []
    for index, question in enumerate(questions):
        unchanged = index < len(previous_questions) and previous_questions[index] == question
        answers.append(
            previous_answers[index] if unchanged and index < len(previous_answers) else ""
        )
    return answers


def _apply_scope_to_session(
    session_row: TicketStudioSession,
    reply: str,
    *,
    apply_tickets: bool = True,
    parent_type: WorkItemType | None = None,
    clear_questions_when_empty: bool = False,
) -> bool:
    summary, questions, items = parse_scope_payload(reply)
    if not items and not summary and not questions:
        return False
    if summary:
        session_row.summary = summary
    if questions:
        # Read the carry-over before overwriting the questions it is compared against.
        answers = _carry_over_answers(session_row, questions)
        session_row.clarifying_questions_json = json.dumps(questions)
        session_row.clarifying_answers_json = json.dumps(answers)
    elif clear_questions_when_empty:
        # The scoper asked nothing this round: close the open round out rather than
        # leaving an answered card on screen blocking generation forever.
        session_row.clarifying_questions_json = "[]"
        session_row.clarifying_answers_json = "[]"
    if items and apply_tickets:
        # An unrepairable proposal never becomes the draft: keep the previous one and
        # report why, rather than storing tickets that every later save would reject.
        items = _repair_draft_for_session(session_row, items, parent_type=parent_type)
        session_row.draft_json = json.dumps([item.model_dump(mode="json") for item in items])
        session_row.clarifying_questions_json = "[]"
        session_row.clarifying_answers_json = "[]"
    return bool(items or summary or questions)


class TicketStudioConflictError(ValueError):
    """Raised when a turn can't start because one is already in flight for the session."""


def apply_settled_turn(
    session: Session,
    session_row: TicketStudioSession,
    reply: str,
    *,
    mode: str,
) -> None:
    """Fold a finished scoper reply into the session, the way its mode requires.

    This is the half of each path that used to run inline after the model call.
    It lives here, keyed by mode, so the background worker applies exactly what
    the request thread used to — no second copy to drift.
    """
    if mode == STUDIO_TURN_CHAT:
        _apply_scope_to_session(session_row, reply, apply_tickets=False)
        return
    if mode in (STUDIO_TURN_CLARIFY, STUDIO_TURN_BOOTSTRAP_CLARIFY):
        _apply_scope_to_session(
            session_row, reply, apply_tickets=False, clear_questions_when_empty=True
        )
        return

    parent = (
        session.get(Ticket, session_row.parent_ticket_id) if session_row.parent_ticket_id else None
    )
    applied = False
    try:
        applied = _apply_scope_to_session(
            session_row,
            reply,
            apply_tickets=True,
            parent_type=parent.work_item_type if parent else None,
        )
    except DraftHierarchyError as exc:
        session_row.summary = (
            "Scope generation returned a ticket hierarchy that cannot be repaired, so the "
            "draft was left unchanged. Generate again, or fix the brief:\n"
            + "\n".join(f"- {violation}" for violation in exc.violations)
        )
        applied = True
    if not applied:
        session_row.summary = (
            session_row.summary
            or "Scope generation did not return structured tickets — refine the brief or chat further."
        )


def has_open_questions(session_row: TicketStudioSession) -> bool:
    return bool(json.loads(session_row.clarifying_questions_json or "[]"))


def _validate_draft_hierarchy(
    items: list[TicketStudioDraftItem],
    *,
    parent_ticket: Ticket | None,
) -> None:
    violations = find_hierarchy_violations(
        items,
        parent_type=parent_ticket.work_item_type if parent_ticket else None,
    )
    if violations:
        raise DraftHierarchyError(violations)


def _apply_draft_workflow(session: Session, ticket: Ticket, template_slug: str) -> None:
    """Put the committed ticket on the workflow the draft asked for.

    create_ticket binds the workspace default, so without this a ticket scoped
    here still runs the full pipeline no matter what it needs. Resetting to the
    first stage is harmless: the ticket was created moments ago.
    """
    slug = (template_slug or "").strip()
    if not slug:
        return
    WorkflowService(session).set_ticket_workflow_template(ticket, slug)


def _record_integration_reviews(
    session: Session, created_ids: list[str], breakdown: dict[str, int]
) -> None:
    """Create the integration-review child for each just-committed feature/milestone,
    appending it to ``created_ids`` and counting it in ``breakdown`` in place."""
    for review_id in ensure_reviews_for_tickets(
        session, list(created_ids), created_by="ticket-studio"
    ):
        created_ids.append(review_id)
        review = session.get(Ticket, review_id)
        if review:
            breakdown[review.work_item_type.value] = (
                breakdown.get(review.work_item_type.value, 0) + 1
            )


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
                        parent_ref=ticket.get("parent_external_id") or None,
                        title=ticket.get("title", "Imported ticket"),
                        description=ticket.get("description", ""),
                        acceptance_criteria=ticket.get("acceptance_criteria", []),
                        priority=ticket.get("priority", 3),
                        selected=True,
                    )
                )
            # Imports arrive as a flat batch of tasks far more often than as a hierarchy;
            # give them their milestone/feature/capability spine up front.
            draft_items = repair_draft_hierarchy(
                draft_items,
                parent_type=parent.work_item_type if parent else None,
                root_title=title,
                root_description=body.brief.strip(),
            )

        reference_repo_ids = self._validated_repo_ids(ws.id, body.reference_repo_ids)

        row = TicketStudioSession(
            workspace_id=ws.id,
            title=title,
            brief=body.brief.strip(),
            reference_repo_ids_json=json.dumps(reference_repo_ids),
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
            "codex_model": body.codex_model.strip(),
            "lmstudio_base_url": body.lmstudio_base_url.strip(),
            "lmstudio_model": body.lmstudio_model.strip(),
        }
        row.runtime_json = json.dumps(payload)
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return get_studio_runtime(row)

    def _validated_repo_ids(self, workspace_id: str, repo_ids: list[str]) -> list[str]:
        """Keep the given ids, in order, minus duplicates and anything outside this
        workspace — a session must not read a repo attached to another project."""
        seen: set[str] = set()
        valid: list[str] = []
        for repo_id in repo_ids:
            if repo_id in seen:
                continue
            repo = self.session.get(ReferenceRepo, repo_id)
            if not repo or repo.workspace_id != workspace_id:
                raise ValueError(f"Reference repo not found in workspace: {repo_id}")
            seen.add(repo_id)
            valid.append(repo_id)
        return valid

    def set_reference_repos(self, session_id: str, repo_ids: list[str]) -> TicketStudioSessionView:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Only draft sessions can be edited")

        valid = self._validated_repo_ids(row.workspace_id, repo_ids)
        detached = set(_load_reference_repo_ids(row)) - set(valid)
        row.reference_repo_ids_json = json.dumps(valid)
        if detached:
            # Findings cite a repo by slug and path; keeping them after that repo is
            # detached would scope tickets against source nobody can open.
            kept_slugs = {repo.slug for repo in ReferenceRepoService(self.session).get_many(valid)}
            survey = [finding for finding in _load_survey(row) if finding.repo_slug in kept_slugs]
            row.survey_json = json.dumps([item.model_dump(mode="json") for item in survey])
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _session_view(self.session, row)

    def generate_survey(self, session_id: str) -> TicketStudioSessionView:
        """Have the scoper read the attached reference repos and report what is worth taking."""
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Cannot survey a committed session")
        if not _readable_reference_dirs(self.session, row):
            raise ValueError("Attach at least one cloned reference repo before surveying")

        prompt = (
            "Survey the attached reference repos against this project and this brief. "
            "Return findings in the JSON survey block."
        )
        self.session.add(TicketStudioMessage(session_id=row.id, role="user", content=prompt))
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()

        try:
            reply = invoke_ticket_studio_model(self.session, row, prompt, mode="survey")
        except Exception as exc:
            reply = f"Ticket studio assistant unavailable: {exc}"

        self.session.add(TicketStudioMessage(session_id=row.id, role="assistant", content=reply))
        summary, questions, findings = parse_survey_payload(reply)
        if findings:
            row.survey_json = json.dumps([item.model_dump(mode="json") for item in findings])
        if summary:
            row.summary = summary
        if questions:
            row.clarifying_questions_json = json.dumps(questions)
            row.clarifying_answers_json = json.dumps(_carry_over_answers(row, questions))
        if not findings and not summary:
            row.summary = (
                "The survey did not return structured findings — check the reference repo "
                "clone, or refine the brief and run it again."
            )
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()

        messages = list_studio_messages(self.session, row.id)
        self.session.refresh(row)
        return _session_view(self.session, row, messages=messages)

    def save_survey(
        self, session_id: str, findings: list[TicketStudioSurveyFinding]
    ) -> TicketStudioSessionView:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Only draft sessions can be edited")

        row.survey_json = json.dumps([item.model_dump(mode="json") for item in findings])
        row.updated_at = datetime.now(timezone.utc)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _session_view(self.session, row)

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

    def _require_idle(self, row: TicketStudioSession) -> None:
        """Refuse a turn while one is in flight.

        Checked before a caller mutates the session as well as inside
        ``_start_turn``: ``save_clarifications`` writes the answers first, and a
        conflict raised after that would leave the request holding changes it
        never meant to keep.
        """
        if latest_pending_studio_turn(self.session, row.id):
            raise TicketStudioConflictError(
                "The scoper is still working on this session — wait for the current turn to finish."
            )

    def _start_turn(
        self,
        row: TicketStudioSession,
        *,
        user_content: str,
        mode: str,
    ) -> TicketStudioSessionView:
        """Record the operator's turn and a pending scoper turn, then return.

        Executes nothing — the caller schedules ``view.active_turn_id`` next.
        Every path that hands work to the scoper goes through here, so none of
        them can go back to running the model on the request thread.
        """
        self._require_idle(row)
        self.session.add(TicketStudioMessage(session_id=row.id, role="user", content=user_content))
        assistant_message = TicketStudioMessage(
            session_id=row.id,
            role="assistant",
            content="",
            status="pending",
            turn_mode=mode,
        )
        self.session.add(assistant_message)
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

        return self._start_turn(row, user_content=text, mode=STUDIO_TURN_CHAT)

    def request_clarifications(
        self, session_id: str, *, auto_scope: bool = False
    ) -> TicketStudioSessionView:
        """Ask the scoper what is unclear.

        ``auto_scope`` is what bootstrapping a brand-new session wants: if the
        scoper comes back with nothing to ask, generate the breakdown rather
        than leaving the operator on an empty draft. The chain lives on the
        turn, not in the caller, so it survives the reload that the caller's
        await would not.
        """
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Cannot clarify a committed session")

        prompt = "Review this feature brief and return clarifying questions only (no tickets yet)."
        mode = STUDIO_TURN_BOOTSTRAP_CLARIFY if auto_scope else STUDIO_TURN_CLARIFY
        return self._start_turn(row, user_content=prompt, mode=mode)

    def save_clarifications(self, session_id: str, answers: list[str]) -> TicketStudioSessionView:
        row = self.session.get(TicketStudioSession, session_id)
        if not row:
            raise ValueError("Ticket studio session not found")
        if row.status != TicketStudioSessionStatus.DRAFT:
            raise ValueError("Only draft sessions can be edited")

        self._require_idle(row)

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
        # The answers join the conversation so that a follow-up round can replace the open
        # questions without discarding what the operator already told the scoper. Answering
        # is the operator's turn; hand straight back to the scoper rather than making them
        # press a button to find out whether anything is still unclear.
        transcript = _format_answers_message(questions, normalized)
        return self._start_turn(row, user_content=transcript, mode=STUDIO_TURN_CLARIFY)

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
        return self._start_turn(row, user_content=prompt, mode=STUDIO_TURN_SCOPE)

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

        for item in topo_sort_draft_items(draft):
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
            _apply_draft_workflow(self.session, created, item.workflow_template_slug)
            ref_to_id[item.ref] = created.id
            created_ids.append(created.id)
            breakdown[item.work_item_type.value] = breakdown.get(item.work_item_type.value, 0) + 1
            if root_ticket_id is None and parent_id is None:
                root_ticket_id = created.id

        # Every feature/milestone we just created gets its integration-review child
        # (a childless capability/feature that depends on its siblings, so it runs
        # last). Post-commit on real tickets — the scope preview stays untouched.
        _record_integration_reviews(self.session, created_ids, breakdown)

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
