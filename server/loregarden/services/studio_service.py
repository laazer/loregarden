"""Agent & Workflow Studio — custom agents and workflow definitions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from loregarden.agents.cli_adapters import build_triage_invocation
from loregarden.agents.registry import AGENTS, get_agent
from loregarden.agents.registry import list_agents as list_builtin_agents
from loregarden.config import settings
from loregarden.models.domain import (
    ClassifyRoute,
    StudioAgent,
    StudioAgentCreate,
    StudioAgentPreview,
    StudioAgentPreviewProfile,
    StudioAgentPreviewRequest,
    StudioAgentUpdate,
    StudioAgentView,
    StudioGateCheck,
    StudioGeneratedAgent,
    StudioGeneratedWorkflow,
    StudioHandoffCheck,
    StudioMcpToolGuide,
    StudioWorkflow,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    StudioWorkflowUpdate,
    StudioWorkflowView,
    Ticket,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.cli_output import extract_triage_reply
from loregarden.services.ticket_studio_service import extract_json_block
from loregarden.services.workflow_service import WorkflowService
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.skills.registry import list_skills
from sqlmodel import Session, select


def _tool_names() -> list[str]:
    from loregarden.mcp.tools import tool_names

    return tool_names()


DEFAULT_STAGE_MCP_TOOLS = [
    "loregarden_get_ticket",
    "loregarden_list_tickets",
    "loregarden_attach_artifact",
    "loregarden_request_approval",
]

DEFAULT_MEMORY_MCP_TOOLS = [
    "loregarden_memory_status",
    "loregarden_search_memory",
    "loregarden_append_learning",
    "loregarden_upsert_memory",
    "loregarden_upsert_blog_post",
    "loregarden_create_memory_relation",
]

STUDIO_ROLE_PREAMBLE = """**Loregarden MCP:** Use MCP tools per `agent_context/agents/common_assets/loregarden_mcp_v1.md` for ticket workflow state.

**Memory protocol:** Read `agent_context/agents/common_assets/memory_protocol_v1.md` — use MCP for memory, learnings, and blog posts (Obsidian + SQLite graph); always pass `workspace_slug`; never write vault or SQLite files directly.
"""

STUDIO_GENERATE_AGENT_ID = "planner"
MAX_STUDIO_GENERATE_CHARS = 4000

AGENT_GENERATE_JSON_SCHEMA = """```json
{
  "name": "Localization Reviewer",
  "slug": "localization-reviewer",
  "description": "One line — when the orchestrator should reach for this agent",
  "role_body": "Detailed role instructions, constraints, and output expectations",
  "adapter": "claude",
  "default_skill": "review",
  "mcp_tools": ["loregarden_get_ticket", "loregarden_attach_artifact"]
}
```"""

WORKFLOW_GENERATE_JSON_SCHEMA = """```json
{
  "name": "Quick Review",
  "slug": "quick-review",
  "description": "Plan then review with optional gate",
  "stages": [
    {
      "key": "plan",
      "name": "Plan",
      "stage_type": "agent",
      "agent_id": "planner",
      "skill_name": "plan",
      "optional": false,
      "order": 1,
      "gate_required": false,
      "classify_routes": []
    }
  ]
}
```"""


def _merge_tool_lists(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for name in group:
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def default_mcp_tools() -> list[str]:
    return _merge_tool_lists(DEFAULT_STAGE_MCP_TOOLS, DEFAULT_MEMORY_MCP_TOOLS)


def _resolve_studio_mcp_tools(raw_tools: list[str] | None, *, mcp_enabled: bool) -> list[str]:
    if not mcp_enabled:
        return []
    base = raw_tools if raw_tools else default_mcp_tools()
    return _merge_tool_lists(base, DEFAULT_MEMORY_MCP_TOOLS)


def _ensure_studio_role_preamble(role_body: str) -> str:
    body = (role_body or "").strip()
    if "memory_protocol_v1.md" in body:
        return body
    if body:
        return f"{STUDIO_ROLE_PREAMBLE}\n{body}"
    return STUDIO_ROLE_PREAMBLE.strip()


DEFAULT_HANDOFF_CHECKS = [
    StudioHandoffCheck(
        kind="mcp_complete",
        prompt="When stage deliverables are ready, ensure tests pass and call loregarden_complete_stage if you are the orchestrator; otherwise finish your role output clearly.",
    ),
    StudioHandoffCheck(
        kind="blocking_clear",
        prompt="Do not hand off with unresolved blocking_issues — document failures or request approval via loregarden_request_approval.",
    ),
]

DEFAULT_GATE_CHECKS = [
    StudioGateCheck(
        kind="workflow_gate",
        title="Stage sign-off",
        impact="Human review required before the workflow advances.",
    ),
]

MCP_TOOL_GUIDES: list[StudioMcpToolGuide] = [
    StudioMcpToolGuide(
        name="loregarden_get_ticket",
        description="Read ticket workflow state, stage map, hierarchy neighbors, and active orchestration run.",
        when_to_use="At stage start and before any workflow decision — never trust stale project_board WORKFLOW STATE alone.",
        example='tools/call loregarden_get_ticket {"ticket_id": "<uuid or external_id slug>", "workspace_slug": "loregarden"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_list_tickets",
        description="Search and list tickets in a workspace for discovery.",
        when_to_use="When you need sibling tasks, child work items, or to find a ticket by title/slug.",
        example='tools/call loregarden_list_tickets {"workspace_slug": "loregarden", "search": "cli runner"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_get_ticket_by_external",
        description="Read ticket state by workspace slug and external_id.",
        when_to_use="When you know the ticket slug (e.g. 03-wire-cli-agent-runner) but not the UUID.",
        example='tools/call loregarden_get_ticket_by_external {"workspace_slug": "loregarden", "external_id": "03-wire-cli-agent-runner"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_start_orchestration",
        description="Start a top-level orchestration run for a ticket.",
        when_to_use="Autopilot / external orchestrator only — not typical stage agents.",
        example='tools/call loregarden_start_orchestration {"ticket_id": "<uuid>", "driver": "external_mcp"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_start_stage",
        description="Mark a workflow stage as running before invoking a sub-agent.",
        when_to_use="Orchestrator only — before delegating to a sub-agent.",
        example='tools/call loregarden_start_stage {"run_id": "<orch run id>", "stage_key": "implementation", "agent_id": "backend_implementer"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_complete_stage",
        description="Mark a stage done and advance the workflow cursor.",
        when_to_use="Orchestrator after a sub-agent succeeds and gates pass. Stage runs from the IDE usually auto-complete.",
        example='tools/call loregarden_complete_stage {"run_id": "<orch run id>", "stage_key": "testing"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_skip_stage",
        description="Mark an optional stage as won't do.",
        when_to_use="Orchestrator when skipping optional stages per workflow rules.",
        example='tools/call loregarden_skip_stage {"run_id": "<orch run id>", "stage_key": "approval", "reason": "Auto-approved"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_block_ticket",
        description="Block the ticket and fail the orchestration run.",
        when_to_use="Unrecoverable failure — document message clearly for operators.",
        example='tools/call loregarden_block_ticket {"run_id": "<orch run id>", "message": "Tests failed after 3 attempts"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_attach_artifact",
        description="Attach log, diff, test output, or other artifact to the ticket.",
        when_to_use="After producing logs, diffs, or structured output the operator should see in the IDE.",
        example='tools/call loregarden_attach_artifact {"run_id": "<run id>", "kind": "log", "title": "Test summary", "content_json": "{\\"lines\\":[]}"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_request_approval",
        description="Create a human approval inbox item for a stage.",
        when_to_use="Before risky/destructive actions or when human sign-off is required by gate checks.",
        example='tools/call loregarden_request_approval {"run_id": "<run id>", "stage_key": "review", "title": "Deploy to staging?", "impact": "Requires operator approval"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_complete_orchestration",
        description="Finish an orchestration run.",
        when_to_use="Orchestrator when all stages are done or the run should terminate.",
        example='tools/call loregarden_complete_orchestration {"run_id": "<orch run id>", "status": "succeeded"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_memory_status",
        description="Discover workspace-scoped Obsidian dirs (memory, learnings, blog posts) and SQLite graph path.",
        when_to_use="Before writing or searching agent memory artifacts — always pass workspace_slug from the run prompt.",
        example='tools/call loregarden_memory_status {"workspace_slug": "loregarden"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_append_learning",
        description="Persist ticket learnings to obsidian_learnings_dir and optional graph SQLite.",
        when_to_use="Learning Agent after Gatekeeper — ticket-scoped insights.",
        example='tools/call loregarden_append_learning {"ticket_id": "03-wire-cli", "workspace_slug": "loregarden", "content": "…"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_upsert_memory",
        description="Upsert durable memory nodes under obsidian_memory_dir and graph SQLite.",
        when_to_use="Patterns, anti-patterns, and reusable knowledge — never write vault files directly.",
        example='tools/call loregarden_upsert_memory {"title": "MCP workflow state", "body": "…", "workspace_slug": "loregarden"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_upsert_blog_post",
        description="Persist human-readable blog post markdown under obsidian_blogposts_dir.",
        when_to_use="Blog Post Agent after learning — retrospective for operators.",
        example='tools/call loregarden_upsert_blog_post {"ticket_id": "03-wire-cli", "workspace_slug": "loregarden", "title": "…", "body": "…"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_create_memory_relation",
        description="Link two memory graph nodes in the workspace SQLite DB (memory_relations table).",
        when_to_use="Learning Agent — use graph.id values from upsert/append responses as source_id and target_id.",
        example='tools/call loregarden_create_memory_relation {"source_id": "<uuid>", "target_id": "<uuid>", "workspace_slug": "loregarden", "relation_type": "supports"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_search_memory",
        description="Search memory, learnings, and blog post notes plus SQLite graph nodes in a workspace.",
        when_to_use="Before acting when prior workspace context may exist (planner, spec, implementers).",
        example='tools/call loregarden_search_memory {"query": "permission bridge", "workspace_slug": "loregarden"}',
        stage_agent=True,
    ),
]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "agent"


_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*(?:\n|$)", re.DOTALL)


def parse_markdown_frontmatter(text: str) -> dict[str, str]:
    body = (text or "").lstrip("\ufeff")
    if not body.startswith("---"):
        return {}
    match = _FRONTMATTER_RE.match(body)
    if not match:
        return {}
    block = match.group(0)
    inner = block.strip().removeprefix("---").removesuffix("---").strip()
    result: dict[str, str] = {}
    for line in inner.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        result[key.strip()] = value.strip()
    return result


def _frontmatter_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    return None


def strip_markdown_frontmatter(text: str) -> str:
    """Remove YAML frontmatter — `---` fences break markdown preview (setext headings)."""
    body = (text or "").lstrip("\ufeff")
    if not body.startswith("---"):
        return text
    match = _FRONTMATTER_RE.match(body)
    if not match:
        return text
    return body[match.end() :].lstrip("\n")


def _parse_json_list(raw: str, model_cls):
    data = json.loads(raw or "[]")
    return [model_cls.model_validate(item) for item in data]


def _load_role_body(role_file: str) -> tuple[str, str]:
    if not role_file:
        return "", ""
    path = settings.agent_context_dir / role_file
    if not path.is_file():
        return "", role_file
    text = path.read_text(encoding="utf-8")
    description = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            description = stripped[:240]
            break
    return text, description


def _agent_view(agent: StudioAgent) -> StudioAgentView:
    raw_tools = json.loads(agent.mcp_tools_json or "[]")
    return StudioAgentView(
        id=agent.id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        role_body=_ensure_studio_role_preamble(agent.role_body),
        role_file="",
        adapter=agent.adapter,
        default_model=agent.default_model,
        timeout=agent.timeout,
        default_skill=agent.default_skill,
        mcp_enabled=agent.mcp_enabled,
        mcp_tools=_resolve_studio_mcp_tools(raw_tools, mcp_enabled=agent.mcp_enabled),
        gate_checks=_parse_json_list(agent.gate_checks_json, StudioGateCheck),
        handoff_checks=_parse_json_list(agent.handoff_checks_json, StudioHandoffCheck),
        built_in=False,
        read_only=False,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _builtin_agent_view(agent_id: str, cfg: dict) -> StudioAgentView:
    now = datetime.now(timezone.utc)
    role_file = str(cfg.get("role_file", ""))
    role_body, excerpt = _load_role_body(role_file)
    return StudioAgentView(
        id=agent_id,
        slug=agent_id,
        name=str(cfg.get("name", agent_id)),
        description=excerpt or "Built-in registry agent",
        role_body=role_body,
        role_file=role_file,
        adapter=str(cfg.get("adapter", "claude")),
        default_model=str(cfg.get("default_model", "")),
        timeout=int(cfg.get("timeout", 600)),
        default_skill="",
        mcp_enabled=True,
        mcp_tools=_tool_names(),
        gate_checks=[],
        handoff_checks=[],
        built_in=True,
        read_only=True,
        created_at=now,
        updated_at=now,
    )


def _workflow_view(session: Session, workflow: StudioWorkflow) -> StudioWorkflowView:
    template_slug = ""
    if workflow.published_template_id:
        tpl = session.get(WorkflowTemplate, workflow.published_template_id)
        if tpl:
            template_slug = tpl.slug
    return StudioWorkflowView(
        id=workflow.id,
        slug=workflow.slug,
        name=workflow.name,
        description=workflow.description,
        stages=[
            StudioWorkflowStage.model_validate(item)
            for item in json.loads(workflow.stages_json or "[]")
        ],
        transitions=json.loads(workflow.transitions_json or "[]"),
        published_template_id=workflow.published_template_id,
        published_template_slug=template_slug,
        built_in=False,
        source_path=f"studio:{workflow.slug}",
        read_only=False,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _template_workflow_view(template: WorkflowTemplate) -> StudioWorkflowView:
    stages_raw = json.loads(template.stages_json or "[]")
    stages: list[StudioWorkflowStage] = []
    for item in stages_raw:
        payload = dict(item)
        payload.setdefault("stage_type", "agent")
        payload.setdefault("classify_routes", [])
        payload.setdefault("parallel_agents", [])
        payload.setdefault("gate_commands", [])
        payload.setdefault("gate_required", False)
        stages.append(StudioWorkflowStage.model_validate(payload))
    return StudioWorkflowView(
        id=template.id,
        slug=template.slug,
        name=template.name,
        description=template.description,
        stages=stages,
        transitions=json.loads(template.transitions_json or "[]"),
        published_template_id=template.id,
        published_template_slug=template.slug,
        built_in=not template.source_path.startswith("studio:"),
        source_path=template.source_path,
        read_only=True,
        created_at=template.created_at,
        updated_at=template.created_at,
    )


def studio_agent_config(session: Session, agent_id: str) -> dict | None:
    agent = session.exec(select(StudioAgent).where(StudioAgent.slug == agent_id)).first()
    if not agent:
        return None
    return _studio_agent_dict(agent)


def _studio_agent_dict(agent: StudioAgent) -> dict:
    raw_tools = json.loads(agent.mcp_tools_json or "[]")
    return {
        "name": agent.name,
        "role_body": _ensure_studio_role_preamble(agent.role_body),
        "adapter": agent.adapter,
        "default_model": agent.default_model,
        "timeout": agent.timeout,
        "default_skill": agent.default_skill,
        "mcp_enabled": agent.mcp_enabled,
        "mcp_tools": _resolve_studio_mcp_tools(raw_tools, mcp_enabled=agent.mcp_enabled),
        "gate_checks": json.loads(agent.gate_checks_json or "[]"),
        "handoff_checks": json.loads(agent.handoff_checks_json or "[]"),
        "studio": True,
    }


def build_studio_prompt_sections(agent_cfg: dict) -> str:
    sections: list[str] = []
    if agent_cfg.get("mcp_enabled", True):
        tools = agent_cfg.get("mcp_tools") or _tool_names()
        sections.extend(
            [
                "## Loregarden MCP tools",
                "Use these MCP tools for ticket workflow state:",
                ", ".join(tools),
                "Read `agent_context/agents/common_assets/loregarden_mcp_v1.md` for full reference.",
                "Read `agent_context/agents/common_assets/memory_protocol_v1.md` for memory, learnings, and blog post paths — MCP only, always pass `workspace_slug`.",
            ]
        )
        guide_map = {item.name: item for item in MCP_TOOL_GUIDES}
        for tool in tools:
            guide = guide_map.get(tool)
            if not guide:
                continue
            sections.extend(
                [
                    f"### {tool}",
                    guide.description,
                    f"**When to use:** {guide.when_to_use}",
                    f"**Example:** `{guide.example}`",
                ]
            )
            if guide.orchestrator_only:
                sections.append(
                    "_Orchestrator-only — stage agents usually should not call this directly._"
                )
    handoffs = agent_cfg.get("handoff_checks") or []
    if handoffs:
        sections.append("## Handoff checks (required before stage completion)")
        for item in handoffs:
            if isinstance(item, dict):
                sections.append(f"- [{item.get('kind', 'check')}] {item.get('prompt', '')}")
    gates = agent_cfg.get("gate_checks") or []
    if gates:
        sections.append("## Gate checks (human approval may be required)")
        for item in gates:
            if isinstance(item, dict):
                sections.append(
                    f"- [{item.get('kind', 'gate')}] {item.get('title', '')}: {item.get('impact', '')}"
                )
    return "\n".join(sections)


def _preview_agent_cfg(body: StudioAgentPreviewRequest) -> dict:
    tools = body.mcp_tools or (default_mcp_tools() if body.mcp_enabled else [])
    return {
        "name": body.name,
        "role_body": _ensure_studio_role_preamble(body.role_body),
        "adapter": body.adapter,
        "timeout": body.timeout,
        "default_skill": body.default_skill,
        "mcp_enabled": body.mcp_enabled,
        "mcp_tools": _resolve_studio_mcp_tools(tools, mcp_enabled=body.mcp_enabled),
        "gate_checks": [item.model_dump() for item in body.gate_checks],
        "handoff_checks": [item.model_dump() for item in body.handoff_checks],
    }


def preview_agent_markdown(body: StudioAgentPreviewRequest) -> StudioAgentPreview:
    cfg = _preview_agent_cfg(body)
    role_frontmatter = parse_markdown_frontmatter(body.role_body)
    role_body = _ensure_studio_role_preamble(strip_markdown_frontmatter(body.role_body)).strip()
    metadata = StudioAgentPreviewProfile(
        description=role_frontmatter.get("description") or body.description.strip(),
        model=role_frontmatter.get("model") or "",
        provider=body.adapter or "claude",
        default_skill=body.default_skill or "",
        timeout=body.timeout,
        always_apply=_frontmatter_bool(role_frontmatter.get("alwaysApply")),
    )
    section_names: list[str] = ["header", "role"]
    parts = [
        "## Agent Role",
        role_body or "_No role instructions yet._",
    ]
    studio_sections = build_studio_prompt_sections(cfg)
    if studio_sections:
        section_names.extend(["mcp_tools", "handoffs", "gates"])
        parts.extend(["", studio_sections])
    mcp_doc_path = settings.agent_context_dir / "agents/common_assets/loregarden_mcp_v1.md"
    if body.mcp_enabled and mcp_doc_path.is_file():
        section_names.append("mcp_module")
        mcp_doc = strip_markdown_frontmatter(mcp_doc_path.read_text(encoding="utf-8"))[:8000]
        parts.extend(["", "## Loregarden MCP module (excerpt)", mcp_doc])
    memory_doc_path = settings.agent_context_dir / "agents/common_assets/memory_protocol_v1.md"
    if body.mcp_enabled and memory_doc_path.is_file():
        section_names.append("memory_protocol_module")
        memory_doc = strip_markdown_frontmatter(memory_doc_path.read_text(encoding="utf-8"))[:8000]
        parts.extend(["", "## Memory protocol module (excerpt)", memory_doc])
    parts.extend(
        [
            "",
            "## Permission policy",
            "Request human approval via Loregarden before destructive or high-risk tool use.",
            "Do not bypass workspace permission checks.",
        ]
    )
    section_names.append("permissions")
    return StudioAgentPreview(
        name=body.name.strip(),
        markdown="\n".join(parts),
        sections=section_names,
        profile=metadata,
    )


# Generic vocabulary that implies a specialty even when the ticket text doesn't
# use the route's literal keyword (e.g. "modal button" implies frontend work).
_SPECIALTY_SYNONYMS: dict[str, list[str]] = {
    "frontend": [
        "ui",
        "component",
        "modal",
        "button",
        "page",
        "screen",
        "css",
        "style",
        "styling",
        "react",
        "client",
        "dialog",
        "tooltip",
        "layout",
        "render",
        "dom",
        "browser",
        "form",
        "dropdown",
        "menu",
        "tab",
        "widget",
    ],
    "backend": [
        "api",
        "endpoint",
        "server",
        "database",
        "db",
        "schema",
        "migration",
        "service",
        "route",
        "controller",
        "query",
        "auth",
        "middleware",
        "sql",
        "orm",
        "cron",
        "worker",
        "queue",
    ],
}


def _word_in_haystack(word: str, haystack: str) -> bool:
    pattern = r"\b" + re.escape(word.lower()) + r"\b"
    return re.search(pattern, haystack) is not None


def _route_match_score(route: ClassifyRoute, haystack: str) -> int | None:
    """Returns the keyword hit count if the route is eligible, else None.

    Specialty is the hard gate: tickets rarely spell out an implementation
    language, but they do describe the domain (buttons, endpoints, etc.), so
    a specialty match is required whenever the route declares one. Language
    only contributes bonus score to break ties between otherwise-eligible
    routes — requiring it outright meant tickets that never mention
    "typescript"/"python"/etc. could never match any language-scoped route.
    """
    spec_words: list[str] = []
    for spec in route.specialties:
        spec_words.append(spec)
        spec_words.extend(_SPECIALTY_SYNONYMS.get(spec.lower(), []))
    spec_hits = [word for word in spec_words if _word_in_haystack(word, haystack)]
    if route.specialties and not spec_hits:
        return None

    lang_hits = [lang for lang in route.languages if _word_in_haystack(lang, haystack)]
    return len(spec_hits) + len(lang_hits)


def resolve_classify_route(ticket: Ticket, stage: WorkflowStageDef) -> tuple[str, str]:
    if stage.stage_type != "classify" or not stage.classify_routes:
        return stage.agent_id, stage.skill_name

    routed = _resolve_next_agent_from_routes(ticket, stage)
    if routed:
        return routed

    acceptance_criteria = ""
    try:
        acceptance_criteria = " ".join(json.loads(ticket.acceptance_criteria_json or "[]"))
    except (TypeError, ValueError):
        pass

    haystack = " ".join(
        [
            ticket.title or "",
            ticket.description or "",
            ticket.external_id or "",
            acceptance_criteria,
        ]
    ).lower()

    default_route: ClassifyRoute | None = None
    best_route: ClassifyRoute | None = None
    best_score = -1
    for route in stage.classify_routes:
        if route.default:
            default_route = route
            continue
        score = _route_match_score(route, haystack)
        if score is not None and score > best_score:
            best_route = route
            best_score = score

    if best_route:
        return best_route.agent_id, best_route.skill_name or stage.skill_name

    if default_route:
        return default_route.agent_id, default_route.skill_name or stage.skill_name

    first = stage.classify_routes[0]
    return first.agent_id, first.skill_name or stage.skill_name


def _resolve_next_agent_from_routes(
    ticket: Ticket,
    stage: WorkflowStageDef,
) -> tuple[str, str] | None:
    next_agent = (ticket.next_agent or "").strip()
    if not next_agent:
        return None

    from loregarden.agents.registry import get_agent

    if not get_agent(next_agent):
        return None

    if stage.classify_routes:
        for route in stage.classify_routes:
            if route.agent_id == next_agent:
                return next_agent, route.skill_name or stage.skill_name
        return None

    return next_agent, stage.skill_name or ""


def _resolve_next_agent_override(ticket: Ticket, stage: WorkflowStageDef) -> tuple[str, str] | None:
    if not (stage.agent_id or "").strip() and stage.stage_type not in {
        "classify",
        "gate",
        "parallel",
    }:
        return None

    next_agent = (ticket.next_agent or "").strip()
    if not next_agent or stage.stage_type in {"parallel", "gate"}:
        return None

    from loregarden.agents.registry import get_agent

    if not get_agent(next_agent):
        return None

    if stage.classify_routes:
        return _resolve_next_agent_from_routes(ticket, stage)

    if stage.stage_type == "classify":
        return _resolve_next_agent_from_routes(ticket, stage)

    if stage.key in {"implementation", "route_impl", "implement"}:
        return next_agent, stage.skill_name or "apply_patch"

    if stage.agent_id and next_agent != stage.agent_id:
        return next_agent, stage.skill_name or ""

    if not stage.agent_id:
        return next_agent, stage.skill_name or ""

    return None


def is_agentless_stage(stage: WorkflowStageDef) -> bool:
    """Stages with no CLI agent (human gates, terminal markers)."""
    if stage.stage_type in {"classify", "gate", "parallel"}:
        return False
    return not (stage.agent_id or "").strip()


def _default_studio_workspace(session: Session) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    if not workspace:
        workspace = session.exec(select(Workspace).order_by(Workspace.slug)).first()
    if not workspace:
        raise ValueError("No workspace available for studio generation")
    return workspace


def resolve_studio_generate_timeout(agent: dict) -> int:
    env = os.environ.get("LOREGARDEN_STUDIO_GENERATE_TIMEOUT")
    if env:
        return max(30, int(env))
    return int(agent.get("timeout", settings.triage_timeout_seconds))


def _available_agent_ids(session: Session) -> list[str]:
    custom = [agent.slug for agent in session.exec(select(StudioAgent)).all()]
    builtin = [item["id"] for item in list_builtin_agents()]
    seen: set[str] = set()
    out: list[str] = []
    for slug in [*custom, *builtin]:
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return sorted(out, key=str.lower)


def _available_skills() -> list[str]:
    return list_skills()


def build_agent_generate_prompt(
    description: str,
    *,
    agent_ids: list[str],
    skills: list[str],
    mcp_tools: list[str],
) -> str:
    trimmed = (description or "").strip()[:MAX_STUDIO_GENERATE_CHARS]
    return "\n".join(
        [
            "# Loregarden Agent Studio",
            "You help operators draft custom Loregarden agents.",
            "Respond with a single fenced JSON block only — no markdown tables or prose outside the block.",
            "",
            "## Operator description",
            trimmed or "—",
            "",
            "## Available agent slugs (for reference only — do not reuse built-in slugs for new agents)",
            ", ".join(agent_ids[:40]),
            "",
            "## Available skills",
            ", ".join(skills) or "—",
            "",
            "## Available MCP tools",
            ", ".join(mcp_tools[:40]),
            "",
            "## Task",
            "Draft a custom agent definition from the operator description.",
            "Choose adapter from: claude, cursor, lmstudio, local.",
            "Pick MCP tools appropriate to the role; prefer loregarden_get_ticket for stage agents.",
            "Write concrete role_body instructions with constraints and expected outputs.",
            "Do not include the Loregarden MCP/memory preamble — the studio adds that automatically.",
            AGENT_GENERATE_JSON_SCHEMA,
        ]
    )


def build_workflow_generate_prompt(
    description: str,
    *,
    agent_ids: list[str],
    skills: list[str],
) -> str:
    trimmed = (description or "").strip()[:MAX_STUDIO_GENERATE_CHARS]
    return "\n".join(
        [
            "# Loregarden Workflow Studio",
            "You help operators draft custom Loregarden workflow pipelines.",
            "Respond with a single fenced JSON block only — no markdown tables or prose outside the block.",
            "",
            "## Operator description",
            trimmed or "—",
            "",
            "## Available agent slugs",
            ", ".join(agent_ids[:40]),
            "",
            "## Available skills",
            ", ".join(skills) or "—",
            "",
            "## Task",
            "Draft a workflow with ordered stages from the operator description.",
            "Use stage_type agent | classify | gate.",
            "For classify stages include classify_routes with languages, specialties, agent_id, skill_name, default.",
            "Use unique stage keys and sequential order values starting at 1.",
            "Prefer existing built-in agents (planner, spec, backend_implementer, gatekeeper, etc.) when they fit.",
            WORKFLOW_GENERATE_JSON_SCHEMA,
        ]
    )


def invoke_studio_generate_model(session: Session, prompt: str) -> str:
    stub = os.environ.get("LOREGARDEN_STUDIO_GENERATE_STUB_RESPONSE")
    if stub is not None:
        return stub

    workspace = _default_studio_workspace(session)
    repo_root = resolve_workspace_root(workspace)
    if not repo_root.is_dir():
        raise ValueError(f"Workspace repo path does not exist: {repo_root}")

    agent = get_agent(STUDIO_GENERATE_AGENT_ID)
    if not agent:
        raise ValueError(f"Unknown studio generate agent: {STUDIO_GENERATE_AGENT_ID}")

    with tempfile.TemporaryDirectory(prefix="loregarden-studio-generate-") as tmp:
        prompt_file = Path(tmp) / "prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        invocation = build_triage_invocation(
            agent_id=STUDIO_GENERATE_AGENT_ID,
            adapter=agent.get("adapter", "claude"),
            prompt=prompt,
            prompt_file=prompt_file,
            skill_name="",
            workspace_root=repo_root,
            workspace=workspace,
        )
        timeout = resolve_studio_generate_timeout(agent)
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
            raise TimeoutError(f"Studio generate assistant timed out after {timeout}s") from None

        if proc.returncode != 0:
            detail = (
                stderr.decode("utf-8", errors="replace").strip()
                or stdout.decode("utf-8", errors="replace").strip()
            )
            raise RuntimeError(detail or f"Studio generate CLI exited with code {proc.returncode}")

        reply = extract_triage_reply(stdout.decode("utf-8", errors="replace"))
        if not reply:
            raise RuntimeError("Studio generate assistant returned an empty response")
        return reply[:12000]


def parse_agent_generate_payload(text: str) -> StudioGeneratedAgent | None:
    payload = extract_json_block(text)
    if not payload:
        return None
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    slug = _slugify(str(payload.get("slug") or name))
    adapter = str(payload.get("adapter") or "claude").strip().lower()
    if adapter not in {"claude", "cursor", "lmstudio", "local"}:
        adapter = "claude"
    tools_raw = payload.get("mcp_tools") or []
    mcp_tools = [str(item).strip() for item in tools_raw if str(item).strip()]
    known_tools = set(_tool_names())
    mcp_tools = [tool for tool in mcp_tools if tool in known_tools]
    return StudioGeneratedAgent(
        name=name,
        slug=slug,
        description=str(payload.get("description") or "").strip(),
        role_body=str(payload.get("role_body") or "").strip(),
        adapter=adapter,
        default_skill=str(payload.get("default_skill") or "").strip(),
        mcp_tools=mcp_tools,
    )


def _normalize_generated_stage(
    raw: dict, *, order: int, agent_ids: set[str], skills: set[str]
) -> StudioWorkflowStage | None:
    key = _slugify(str(raw.get("key") or f"stage_{order}"))
    name = str(raw.get("name") or key.replace("-", " ").title()).strip()
    stage_type = str(raw.get("stage_type") or "agent").strip().lower()
    if stage_type not in {"agent", "classify", "gate"}:
        stage_type = "agent"
    agent_id = str(raw.get("agent_id") or "").strip()
    skill_name = str(raw.get("skill_name") or "").strip()
    if stage_type == "gate" and not agent_id:
        agent_id = "gatekeeper"
        skill_name = skill_name or "ac_gate"
    if stage_type == "agent" and agent_id and agent_id not in agent_ids:
        agent_id = "planner" if "planner" in agent_ids else next(iter(agent_ids), "planner")
    if skill_name and skill_name not in skills:
        skill_name = ""
    routes: list[ClassifyRoute] = []
    for route_raw in raw.get("classify_routes") or []:
        if not isinstance(route_raw, dict):
            continue
        route_agent = str(route_raw.get("agent_id") or "").strip()
        if route_agent and route_agent not in agent_ids:
            route_agent = "backend_implementer" if "backend_implementer" in agent_ids else agent_id
        route_skill = str(route_raw.get("skill_name") or "").strip()
        if route_skill and route_skill not in skills:
            route_skill = "apply_patch" if "apply_patch" in skills else route_skill
        routes.append(
            ClassifyRoute(
                languages=[
                    str(item).strip()
                    for item in (route_raw.get("languages") or [])
                    if str(item).strip()
                ],
                specialties=[
                    str(item).strip()
                    for item in (route_raw.get("specialties") or [])
                    if str(item).strip()
                ],
                agent_id=route_agent or "backend_implementer",
                skill_name=route_skill,
                default=bool(route_raw.get("default")),
            )
        )
    if stage_type == "classify" and not routes:
        routes = [
            ClassifyRoute(
                languages=["python"],
                specialties=["backend"],
                agent_id="backend_implementer"
                if "backend_implementer" in agent_ids
                else agent_id or "planner",
                skill_name="apply_patch" if "apply_patch" in skills else skill_name,
                default=True,
            )
        ]
    return StudioWorkflowStage(
        key=key,
        name=name,
        stage_type=stage_type,
        agent_id=agent_id,
        skill_name=skill_name,
        optional=bool(raw.get("optional")),
        order=order,
        gate_required=bool(raw.get("gate_required")),
        classify_routes=routes,
    )


def parse_workflow_generate_payload(
    text: str,
    *,
    agent_ids: list[str],
    skills: list[str],
) -> StudioGeneratedWorkflow | None:
    payload = extract_json_block(text)
    if not payload:
        return None
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    slug = _slugify(str(payload.get("slug") or name))
    agent_id_set = set(agent_ids)
    skill_set = set(skills)
    stages: list[StudioWorkflowStage] = []
    for index, raw in enumerate(payload.get("stages") or [], start=1):
        if not isinstance(raw, dict):
            continue
        stage = _normalize_generated_stage(
            raw,
            order=index,
            agent_ids=agent_id_set,
            skills=skill_set,
        )
        if stage:
            stages.append(stage)
    if not stages:
        stages = [
            StudioWorkflowStage(
                key="plan",
                name="Plan",
                stage_type="agent",
                agent_id="planner" if "planner" in agent_id_set else agent_ids[0],
                skill_name="plan" if "plan" in skill_set else "",
                optional=False,
                order=1,
                gate_required=False,
                classify_routes=[],
            )
        ]
    return StudioGeneratedWorkflow(
        name=name,
        slug=slug,
        description=str(payload.get("description") or "").strip(),
        stages=stages,
    )


def resolve_stage_execution(ticket: Ticket, stage: WorkflowStageDef) -> tuple[str, str]:
    if stage.stage_type == "classify":
        return resolve_classify_route(ticket, stage)
    if stage.stage_type == "gate":
        return stage.agent_id or "gatekeeper", stage.skill_name or "ac_gate"
    if stage.stage_type == "parallel":
        return "", ""
    routed = _resolve_next_agent_override(ticket, stage)
    if routed:
        return routed
    return stage.agent_id, stage.skill_name


class StudioService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_mcp_tools(self) -> list[str]:
        return _tool_names()

    def list_mcp_tool_guides(self) -> list[StudioMcpToolGuide]:
        known = {item.name for item in MCP_TOOL_GUIDES}
        guides = list(MCP_TOOL_GUIDES)
        for name in _tool_names():
            if name not in known:
                guides.append(
                    StudioMcpToolGuide(
                        name=name,
                        description="Loregarden MCP tool",
                        when_to_use="See loregarden_mcp_v1.md for usage.",
                        example=f"tools/call {name} {{}}",
                    )
                )
        return guides

    def agent_defaults(self) -> dict:
        return {
            "mcp_tools": default_mcp_tools(),
            "memory_mcp_tools": DEFAULT_MEMORY_MCP_TOOLS,
            "handoff_checks": [item.model_dump() for item in DEFAULT_HANDOFF_CHECKS],
            "gate_checks": [item.model_dump() for item in DEFAULT_GATE_CHECKS],
        }

    def preview_agent(self, body: StudioAgentPreviewRequest) -> StudioAgentPreview:
        return preview_agent_markdown(body)

    def generate_agent(self, description: str) -> StudioGeneratedAgent:
        trimmed = (description or "").strip()
        if not trimmed:
            raise ValueError("Description is required")
        prompt = build_agent_generate_prompt(
            trimmed,
            agent_ids=_available_agent_ids(self.session),
            skills=_available_skills(),
            mcp_tools=_tool_names(),
        )
        reply = invoke_studio_generate_model(self.session, prompt)
        generated = parse_agent_generate_payload(reply)
        if not generated:
            raise ValueError("Could not parse agent draft from assistant response")
        if generated.slug in AGENTS:
            generated.slug = _slugify(f"{generated.slug}-custom")
        return generated

    def generate_workflow(self, description: str) -> StudioGeneratedWorkflow:
        trimmed = (description or "").strip()
        if not trimmed:
            raise ValueError("Description is required")
        agent_ids = _available_agent_ids(self.session)
        skills = _available_skills()
        prompt = build_workflow_generate_prompt(trimmed, agent_ids=agent_ids, skills=skills)
        reply = invoke_studio_generate_model(self.session, prompt)
        generated = parse_workflow_generate_payload(reply, agent_ids=agent_ids, skills=skills)
        if not generated:
            raise ValueError("Could not parse workflow draft from assistant response")
        return generated

    def list_agents(self, *, include_builtin: bool = True) -> list[StudioAgentView]:
        custom = [
            _agent_view(agent)
            for agent in self.session.exec(select(StudioAgent).order_by(StudioAgent.name)).all()
        ]
        if not include_builtin:
            return custom
        builtin_ids = {item.slug for item in custom}
        merged = list(custom)
        for item in list_builtin_agents():
            if item["id"] in builtin_ids:
                continue
            merged.append(_builtin_agent_view(item["id"], item))
        return sorted(merged, key=lambda item: (not item.built_in, item.name.lower()))

    def get_agent(self, slug: str) -> StudioAgentView | None:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if agent:
            return _agent_view(agent)
        cfg = AGENTS.get(slug)
        if cfg:
            return _builtin_agent_view(slug, cfg)
        return None

    def create_agent(self, body: StudioAgentCreate) -> StudioAgentView:
        slug = _slugify(body.slug)
        if self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first():
            raise ValueError(f"Studio agent already exists: {slug}")
        if slug in AGENTS:
            raise ValueError(f"Slug conflicts with built-in agent: {slug}")
        now = datetime.now(timezone.utc)
        mcp_tools = body.mcp_tools if body.mcp_tools else default_mcp_tools()
        handoffs = body.handoff_checks if body.handoff_checks else DEFAULT_HANDOFF_CHECKS
        agent = StudioAgent(
            slug=slug,
            name=body.name.strip(),
            description=body.description.strip(),
            role_body=_ensure_studio_role_preamble(body.role_body),
            adapter=body.adapter or "claude",
            default_model=body.default_model,
            timeout=body.timeout,
            default_skill=body.default_skill,
            mcp_enabled=body.mcp_enabled,
            mcp_tools_json=json.dumps(mcp_tools),
            gate_checks_json=json.dumps([item.model_dump() for item in body.gate_checks]),
            handoff_checks_json=json.dumps([item.model_dump() for item in handoffs]),
            created_at=now,
            updated_at=now,
        )
        self.session.add(agent)
        self.session.commit()
        self.session.refresh(agent)
        return _agent_view(agent)

    def update_agent(self, slug: str, body: StudioAgentUpdate) -> StudioAgentView:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        if body.name is not None:
            agent.name = body.name.strip()
        if body.description is not None:
            agent.description = body.description.strip()
        if body.role_body is not None:
            agent.role_body = _ensure_studio_role_preamble(body.role_body)
        if body.adapter is not None:
            agent.adapter = body.adapter
        if body.default_model is not None:
            agent.default_model = body.default_model
        if body.timeout is not None:
            agent.timeout = body.timeout
        if body.default_skill is not None:
            agent.default_skill = body.default_skill
        if body.mcp_enabled is not None:
            agent.mcp_enabled = body.mcp_enabled
        if body.mcp_tools is not None:
            agent.mcp_tools_json = json.dumps(body.mcp_tools)
        if body.gate_checks is not None:
            agent.gate_checks_json = json.dumps([item.model_dump() for item in body.gate_checks])
        if body.handoff_checks is not None:
            agent.handoff_checks_json = json.dumps(
                [item.model_dump() for item in body.handoff_checks]
            )
        agent.updated_at = datetime.now(timezone.utc)
        self.session.add(agent)
        self.session.commit()
        self.session.refresh(agent)
        return _agent_view(agent)

    def delete_agent(self, slug: str) -> None:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        self.session.delete(agent)
        self.session.commit()

    def list_workflows(self) -> list[StudioWorkflowView]:
        custom = [
            _workflow_view(self.session, item)
            for item in self.session.exec(
                select(StudioWorkflow).order_by(StudioWorkflow.name)
            ).all()
        ]
        custom_slugs = {item.slug for item in custom}
        published_slugs = {
            item.published_template_slug for item in custom if item.published_template_slug
        }
        merged = list(custom)
        for template in WorkflowService(self.session).list_templates():
            if template.slug in published_slugs:
                continue
            if template.source_path.startswith("studio:"):
                studio_slug = template.source_path.removeprefix("studio:")
                if studio_slug in custom_slugs:
                    continue
            merged.append(_template_workflow_view(template))
        return sorted(merged, key=lambda item: (not item.built_in, item.name.lower()))

    def get_workflow(self, slug: str) -> StudioWorkflowView | None:
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if workflow:
            return _workflow_view(self.session, workflow)
        template = WorkflowService(self.session).get_template_by_slug(slug)
        if template:
            return _template_workflow_view(template)
        return None

    def create_workflow(self, body: StudioWorkflowCreate) -> StudioWorkflowView:
        slug = _slugify(body.slug)
        if self.session.exec(select(StudioWorkflow).where(StudioWorkflow.slug == slug)).first():
            raise ValueError(f"Studio workflow already exists: {slug}")
        stages = sorted(body.stages, key=lambda stage: stage.order)
        transitions = body.transitions or _auto_transitions(stages)
        now = datetime.now(timezone.utc)
        workflow = StudioWorkflow(
            slug=slug,
            name=body.name.strip(),
            description=body.description.strip(),
            stages_json=json.dumps([stage.model_dump() for stage in stages]),
            transitions_json=json.dumps(transitions),
            created_at=now,
            updated_at=now,
        )
        self.session.add(workflow)
        self.session.commit()
        self.session.refresh(workflow)
        return _workflow_view(self.session, workflow)

    def update_workflow(self, slug: str, body: StudioWorkflowUpdate) -> StudioWorkflowView:
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if not workflow:
            raise ValueError(f"Studio workflow not found: {slug}")
        if body.name is not None:
            workflow.name = body.name.strip()
        if body.description is not None:
            workflow.description = body.description.strip()
        if body.stages is not None:
            stages = sorted(body.stages, key=lambda stage: stage.order)
            workflow.stages_json = json.dumps([stage.model_dump() for stage in stages])
            if body.transitions is None:
                workflow.transitions_json = json.dumps(_auto_transitions(stages))
        if body.transitions is not None:
            workflow.transitions_json = json.dumps(body.transitions)
        workflow.updated_at = datetime.now(timezone.utc)
        self.session.add(workflow)
        self.session.commit()
        self.session.refresh(workflow)
        return _workflow_view(self.session, workflow)

    def delete_workflow(self, slug: str) -> None:
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if not workflow:
            raise ValueError(f"Studio workflow not found: {slug}")
        self.session.delete(workflow)
        self.session.commit()

    def publish_workflow(self, slug: str) -> StudioWorkflowView:
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if not workflow:
            raise ValueError(f"Studio workflow not found: {slug}")
        stages = [
            StudioWorkflowStage.model_validate(item)
            for item in json.loads(workflow.stages_json or "[]")
        ]
        if not stages:
            raise ValueError("Workflow must have at least one stage")

        published_slug = f"studio-{workflow.slug}"
        stage_defs: list[dict] = []
        for stage in sorted(stages, key=lambda item: item.order):
            agent_id = stage.agent_id
            skill_name = stage.skill_name
            if stage.stage_type == "classify" and stage.classify_routes:
                default = next(
                    (route for route in stage.classify_routes if route.default),
                    stage.classify_routes[0],
                )
                agent_id = agent_id or default.agent_id
                skill_name = skill_name or default.skill_name
            stage_defs.append(
                {
                    "key": stage.key,
                    "name": stage.name,
                    "agent_id": agent_id,
                    "skill_name": skill_name,
                    "optional": stage.optional,
                    "order": stage.order,
                    "stage_type": stage.stage_type,
                    "classify_routes": [route.model_dump() for route in stage.classify_routes],
                    "parallel_agents": [item.model_dump() for item in stage.parallel_agents],
                    "gate_commands": list(stage.gate_commands),
                    "gate_required": stage.gate_required,
                    "model": stage.model,
                }
            )

        transitions = json.loads(workflow.transitions_json or "[]")
        if not transitions:
            transitions = _auto_transitions(stages)

        template = self.session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == published_slug)
        ).first()
        if template:
            template.name = workflow.name
            template.description = workflow.description or f"Studio workflow · {workflow.slug}"
            template.stages_json = json.dumps(stage_defs)
            template.transitions_json = json.dumps(transitions)
            template.source_path = f"studio:{workflow.slug}"
        else:
            template = WorkflowTemplate(
                slug=published_slug,
                name=workflow.name,
                description=workflow.description or f"Studio workflow · {workflow.slug}",
                stages_json=json.dumps(stage_defs),
                transitions_json=json.dumps(transitions),
                source_path=f"studio:{workflow.slug}",
            )
            self.session.add(template)
            self.session.flush()

        workflow.published_template_id = template.id
        workflow.updated_at = datetime.now(timezone.utc)
        self.session.add(workflow)
        self.session.commit()
        self.session.refresh(workflow)
        return _workflow_view(self.session, workflow)


def _auto_transitions(stages: list[StudioWorkflowStage]) -> list[dict[str, str]]:
    ordered = sorted(stages, key=lambda stage: stage.order)
    transitions: list[dict[str, str]] = []
    for idx in range(len(ordered) - 1):
        transitions.append({"from": ordered[idx].key, "to": ordered[idx + 1].key})
    return transitions


def load_studio_agent_config(agent_id: str) -> dict | None:
    from loregarden.db.session import engine

    with Session(engine) as session:
        return studio_agent_config(session, agent_id)
