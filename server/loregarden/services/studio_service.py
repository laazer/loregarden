"""Agent & Workflow Studio — custom agents and workflow definitions."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.agents.registry import AGENTS
from loregarden.agents.registry import list_agents as list_builtin_agents
from loregarden.config import settings
from loregarden.core.workflow_loader import write_template_version
from loregarden.models.domain import (
    StudioAgent,
    StudioAgentCreate,
    StudioAgentPreview,
    StudioAgentPreviewProfile,
    StudioAgentPreviewRequest,
    StudioAgentUpdate,
    StudioAgentVersion,
    StudioAgentVersionView,
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
    StudioWorkflowVersionView,
    StudioWorkflowView,
    WorkflowTemplate,
    WorkflowTemplateVersion,
)
from loregarden.services.studio_generation import (
    build_agent_generate_prompt,
    build_workflow_generate_prompt,
    invoke_studio_generate_model,
    parse_agent_generate_payload,
    parse_workflow_generate_payload,
    slugify,
    tool_names,
)
from loregarden.services.studio_routing import SKIP_CONDITIONS
from loregarden.services.workflow_service import WorkflowService
from loregarden.skills.registry import list_skills
from sqlmodel import Session, select

DEFAULT_STAGE_MCP_TOOLS = [
    "loregarden_get_ticket",
    "loregarden_list_tickets",
    "loregarden_attach_artifact",
    # A stage that must produce evidence needs the tool to record it, or it is
    # blocked with no way to comply.
    "loregarden_attach_evidence",
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
        name="loregarden_attach_evidence",
        description="Attach proof that the work behaves as claimed, stamped with the commit it proves.",
        when_to_use=(
            "When you can show the change working, not just that tests pass: a red-to-green "
            "test, output from the real surface (HTTP response, screenshot, DB row), or a "
            "verifier's verdict."
        ),
        example=(
            'tools/call loregarden_attach_evidence {"run_id": "<run id>", '
            '"evidence_kind": "real_surface", "title": "POST /api/tickets returns 201", '
            '"content_json": "{"status": 201}"}'
        ),
        stage_agent=True,
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


def load_role_body(role_file: str) -> tuple[str, str]:
    if not role_file:
        return "", ""
    path = settings.agent_context_dir / role_file
    if not path.is_file():
        return "", role_file
    text = path.read_text(encoding="utf-8")
    # Prefer the frontmatter `description:`; otherwise the first prose line of the
    # body (frontmatter fences stripped so `---` never becomes the description).
    description = parse_markdown_frontmatter(text).get("description", "")
    if not description:
        for line in strip_markdown_frontmatter(text).splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                description = stripped[:240]
                break
    return text, description


logger = logging.getLogger(__name__)

# Fields captured verbatim in each StudioAgentVersion snapshot. Must match the
# migration backfill (0022) so restore round-trips cleanly.
_AGENT_SNAPSHOT_FIELDS = (
    "slug",
    "name",
    "description",
    "role_body",
    "adapter",
    "default_model",
    "timeout",
    "default_skill",
    "mcp_enabled",
    "mcp_tools_json",
    "gate_checks_json",
    "handoff_checks_json",
    "built_in",
)


def _agent_snapshot(agent: StudioAgent) -> dict:
    return agent.model_dump(include=set(_AGENT_SNAPSHOT_FIELDS))


def _write_agent_version(
    session: Session, agent: StudioAgent, *, created_by: str, change_note: str = ""
) -> None:
    session.add(
        StudioAgentVersion(
            id=str(uuid4()),
            agent_id=agent.id,
            version=agent.version,
            snapshot_json=json.dumps(_agent_snapshot(agent)),
            created_by=created_by,
            change_note=change_note or "",
        )
    )


def seed_builtin_agents(session: Session) -> list[str]:
    """Idempotently seed the registry built-ins into ``studio_agents`` so the DB is
    the single source of truth. Seed-WHEN-MISSING by slug — an existing row (edited
    or not) is never overwritten, preserving user edits and version history. Returns
    the slugs newly seeded.
    """
    existing = {a.slug for a in session.exec(select(StudioAgent)).all()}
    seeded: list[str] = []
    for slug, cfg in AGENTS.items():
        if slug in existing:
            continue
        role_file = str(cfg.get("role_file", ""))
        role_body, excerpt = load_role_body(role_file)
        if not role_body:
            logger.warning(
                "seed_builtin_agents: role file missing/empty for agent %r (%s); "
                "seeding with an empty role body",
                slug,
                role_file,
            )
        agent = StudioAgent(
            id=str(uuid4()),
            slug=slug,
            name=str(cfg.get("name", slug)),
            description=excerpt or "",
            role_body=role_body,
            adapter=str(cfg.get("adapter", "claude")),
            # Registry pins model under `claude_model` (e.g. triage→haiku); wire it
            # into default_model, which is the key the executor actually reads.
            default_model=str(cfg.get("claude_model", "") or cfg.get("default_model", "")),
            timeout=int(cfg.get("timeout", 600)),
            default_skill="",
            mcp_enabled=True,
            # Preserve the prior built-in behavior of listing all MCP tools.
            mcp_tools_json=json.dumps(tool_names()),
            gate_checks_json="[]",
            handoff_checks_json="[]",
            version=1,
            built_in=True,
        )
        session.add(agent)
        session.flush()
        _write_agent_version(session, agent, created_by="seed")
        seeded.append(slug)
    if seeded:
        session.commit()
    return seeded


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
        built_in=agent.built_in,
        read_only=False,
        version=agent.version,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _agent_snapshot_view(agent: StudioAgent, snap: dict) -> StudioAgentView:
    """Render a historical agent snapshot (read-only) for the version-detail view."""
    mcp_enabled = bool(snap.get("mcp_enabled", True))
    raw_tools = json.loads(snap.get("mcp_tools_json") or "[]")
    return StudioAgentView(
        id=agent.id,
        slug=snap.get("slug", agent.slug),
        name=snap.get("name", ""),
        description=snap.get("description", ""),
        role_body=_ensure_studio_role_preamble(snap.get("role_body", "")),
        role_file="",
        adapter=snap.get("adapter", "claude"),
        default_model=snap.get("default_model", ""),
        timeout=int(snap.get("timeout", 600)),
        default_skill=snap.get("default_skill", ""),
        mcp_enabled=mcp_enabled,
        mcp_tools=_resolve_studio_mcp_tools(raw_tools, mcp_enabled=mcp_enabled),
        gate_checks=_parse_json_list(snap.get("gate_checks_json", "[]"), StudioGateCheck),
        handoff_checks=_parse_json_list(snap.get("handoff_checks_json", "[]"), StudioHandoffCheck),
        built_in=bool(snap.get("built_in", False)),
        read_only=True,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _builtin_agent_view(agent_id: str, cfg: dict) -> StudioAgentView:
    now = datetime.now(timezone.utc)
    role_file = str(cfg.get("role_file", ""))
    role_body, excerpt = load_role_body(role_file)
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
        mcp_tools=tool_names(),
        gate_checks=[],
        handoff_checks=[],
        built_in=True,
        read_only=True,
        created_at=now,
        updated_at=now,
    )


def _workflow_view(session: Session, workflow: StudioWorkflow) -> StudioWorkflowView:
    template_slug = ""
    template_version = 1
    if workflow.published_template_id:
        tpl = session.get(WorkflowTemplate, workflow.published_template_id)
        if tpl:
            template_slug = tpl.slug
            template_version = tpl.version
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
        version=template_version,
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
        version=template.version,
        created_at=template.created_at,
        updated_at=template.created_at,
    )


def _template_snapshot_view(template: WorkflowTemplate, snap: dict) -> StudioWorkflowView:
    """Render a historical template snapshot (read-only) for the version-detail view."""
    stages: list[StudioWorkflowStage] = []
    for item in json.loads(snap.get("stages_json") or "[]"):
        payload = dict(item)
        payload.setdefault("stage_type", "agent")
        payload.setdefault("classify_routes", [])
        payload.setdefault("parallel_agents", [])
        payload.setdefault("gate_commands", [])
        payload.setdefault("gate_required", False)
        stages.append(StudioWorkflowStage.model_validate(payload))
    source_path = snap.get("source_path", "")
    return StudioWorkflowView(
        id=template.id,
        slug=snap.get("slug", template.slug),
        name=snap.get("name", ""),
        description=snap.get("description", ""),
        stages=stages,
        transitions=json.loads(snap.get("transitions_json") or "[]"),
        published_template_id=template.id,
        published_template_slug=template.slug,
        built_in=bool(snap.get("built_in", not source_path.startswith("studio:"))),
        source_path=source_path,
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
        tools = agent_cfg.get("mcp_tools") or tool_names()
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


def _collect_stage_agent_ids(stages: list[StudioWorkflowStage]) -> set[str]:
    ids: set[str] = set()
    for stage in stages:
        if stage.agent_id:
            ids.add(stage.agent_id)
        for route in stage.classify_routes or []:
            if route.agent_id:
                ids.add(route.agent_id)
        for spec in stage.parallel_agents or []:
            if spec.agent_id:
                ids.add(spec.agent_id)
    return ids


def _validate_stage_agent_ids(session: Session, stages: list[StudioWorkflowStage]) -> None:
    """Reject a workflow whose stages reference agents that do not exist. Nothing
    validated this before, which let a template ship pointing at a missing agent."""
    available = set(_available_agent_ids(session))
    unknown = sorted(_collect_stage_agent_ids(stages) - available)
    if unknown:
        raise ValueError(f"Workflow references unknown agent(s): {', '.join(unknown)}")


def _validate_stage_route_targets(stages: list[StudioWorkflowStage]) -> None:
    """Reject a classify branch pointing at a stage the workflow doesn't have.

    A phantom target would otherwise raise at routing time, mid-run, not on save.
    """
    keys = {stage.key for stage in stages}
    unknown = sorted(
        {
            route.to_stage
            for stage in stages
            for route in stage.classify_routes or []
            if route.to_stage and route.to_stage not in keys
        }
    )
    if unknown:
        raise ValueError(f"Workflow routes branch to unknown stage(s): {', '.join(unknown)}")


def _available_skills() -> list[str]:
    return list_skills()


class StudioService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_mcp_tools(self) -> list[str]:
        return tool_names()

    def list_mcp_tool_guides(self) -> list[StudioMcpToolGuide]:
        known = {item.name for item in MCP_TOOL_GUIDES}
        guides = list(MCP_TOOL_GUIDES)
        for name in tool_names():
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
            # Served rather than mirrored in the client so the vocabulary has one
            # source of truth; a hardcoded TS copy would drift from the resolver.
            "skip_conditions": list(SKIP_CONDITIONS),
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
            mcp_tools=tool_names(),
        )
        reply = invoke_studio_generate_model(self.session, prompt)
        generated = parse_agent_generate_payload(reply)
        if not generated:
            raise ValueError("Could not parse agent draft from assistant response")
        if generated.slug in AGENTS:
            generated.slug = slugify(f"{generated.slug}-custom")
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
        slug = slugify(body.slug)
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
            version=1,
            built_in=False,
            created_at=now,
            updated_at=now,
        )
        self.session.add(agent)
        self.session.flush()
        _write_agent_version(self.session, agent, created_by="studio-ui")
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
        agent.version += 1
        self.session.add(agent)
        self.session.flush()
        _write_agent_version(
            self.session, agent, created_by="studio-ui", change_note=body.change_note or ""
        )
        self.session.commit()
        self.session.refresh(agent)
        return _agent_view(agent)

    def delete_agent(self, slug: str) -> None:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        for version in self.session.exec(
            select(StudioAgentVersion).where(StudioAgentVersion.agent_id == agent.id)
        ).all():
            self.session.delete(version)
        self.session.delete(agent)
        self.session.commit()

    def list_agent_versions(self, slug: str) -> list[StudioAgentVersionView]:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        rows = self.session.exec(
            select(StudioAgentVersion)
            .where(StudioAgentVersion.agent_id == agent.id)
            .order_by(StudioAgentVersion.version.desc())
        ).all()
        return [
            StudioAgentVersionView(
                version=row.version,
                created_by=row.created_by,
                change_note=row.change_note,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def get_agent_version(self, slug: str, version: int) -> StudioAgentVersionView:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        row = self.session.exec(
            select(StudioAgentVersion).where(
                StudioAgentVersion.agent_id == agent.id, StudioAgentVersion.version == version
            )
        ).first()
        if not row:
            raise ValueError(f"Version {version} not found for agent {slug}")
        snap = json.loads(row.snapshot_json or "{}")
        return StudioAgentVersionView(
            version=row.version,
            created_by=row.created_by,
            change_note=row.change_note,
            created_at=row.created_at,
            snapshot=_agent_snapshot_view(agent, snap),
        )

    def restore_agent_version(self, slug: str, version: int) -> StudioAgentView:
        """Apply an old snapshot as a NEW head version. History is never mutated."""
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        row = self.session.exec(
            select(StudioAgentVersion).where(
                StudioAgentVersion.agent_id == agent.id, StudioAgentVersion.version == version
            )
        ).first()
        if not row:
            raise ValueError(f"Version {version} not found for agent {slug}")
        snap = json.loads(row.snapshot_json or "{}")
        restored = {
            field: snap[field]
            for field in _AGENT_SNAPSHOT_FIELDS
            if field != "slug" and field in snap
        }
        agent.sqlmodel_update(restored)
        agent.version += 1
        agent.updated_at = datetime.now(timezone.utc)
        self.session.add(agent)
        self.session.flush()
        _write_agent_version(
            self.session, agent, created_by="studio-ui", change_note=f"Restored from v{version}"
        )
        self.session.commit()
        self.session.refresh(agent)
        return _agent_view(agent)

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
        slug = slugify(body.slug)
        if self.session.exec(select(StudioWorkflow).where(StudioWorkflow.slug == slug)).first():
            raise ValueError(f"Studio workflow already exists: {slug}")
        stages = sorted(body.stages, key=lambda stage: stage.order)
        _validate_stage_agent_ids(self.session, stages)
        _validate_stage_route_targets(stages)
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
            _validate_stage_agent_ids(self.session, stages)
            _validate_stage_route_targets(stages)
            workflow.stages_json = json.dumps([stage.model_dump() for stage in stages])
            if body.transitions is None:
                # Editing a stage must not destroy hand-authored routes. This used to
                # regenerate a bare linear chain on every stage edit, silently dropping
                # every `when: reject` edge — the only thing that routes rework to the
                # stage a rejecting agent actually asked for. Seed a linear chain only
                # when there is nothing to preserve; otherwise keep the existing routes,
                # minus any that now point at a stage that no longer exists.
                existing = json.loads(workflow.transitions_json or "[]")
                workflow.transitions_json = json.dumps(
                    _prune_transitions(existing, stages) if existing else _auto_transitions(stages)
                )
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
        _validate_stage_agent_ids(self.session, stages)
        _validate_stage_route_targets(stages)

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
            template.built_in = False
            template.version += 1
        else:
            template = WorkflowTemplate(
                slug=published_slug,
                name=workflow.name,
                description=workflow.description or f"Studio workflow · {workflow.slug}",
                stages_json=json.dumps(stage_defs),
                transitions_json=json.dumps(transitions),
                source_path=f"studio:{workflow.slug}",
                version=1,
                built_in=False,
            )
            self.session.add(template)
            self.session.flush()
        write_template_version(self.session, template, created_by="studio-ui")

        workflow.published_template_id = template.id
        workflow.updated_at = datetime.now(timezone.utc)
        self.session.add(workflow)
        self.session.commit()
        self.session.refresh(workflow)
        return _workflow_view(self.session, workflow)

    def _resolve_workflow_template(self, slug: str) -> WorkflowTemplate | None:
        """Resolve a workflow slug to its versioned template: a studio draft via its
        published template, else a template slug directly."""
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if workflow and workflow.published_template_id:
            return self.session.get(WorkflowTemplate, workflow.published_template_id)
        return self.session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == slug)
        ).first()

    def list_workflow_versions(self, slug: str) -> list[StudioWorkflowVersionView]:
        template = self._resolve_workflow_template(slug)
        if not template:
            raise ValueError(f"Workflow not found or unpublished: {slug}")
        rows = self.session.exec(
            select(WorkflowTemplateVersion)
            .where(WorkflowTemplateVersion.template_id == template.id)
            .order_by(WorkflowTemplateVersion.version.desc())
        ).all()
        return [
            StudioWorkflowVersionView(
                version=row.version,
                created_by=row.created_by,
                change_note=row.change_note,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def get_workflow_version(self, slug: str, version: int) -> StudioWorkflowVersionView:
        template = self._resolve_workflow_template(slug)
        if not template:
            raise ValueError(f"Workflow not found or unpublished: {slug}")
        row = self.session.exec(
            select(WorkflowTemplateVersion).where(
                WorkflowTemplateVersion.template_id == template.id,
                WorkflowTemplateVersion.version == version,
            )
        ).first()
        if not row:
            raise ValueError(f"Version {version} not found for workflow {slug}")
        snap = json.loads(row.snapshot_json or "{}")
        return StudioWorkflowVersionView(
            version=row.version,
            created_by=row.created_by,
            change_note=row.change_note,
            created_at=row.created_at,
            snapshot=_template_snapshot_view(template, snap),
        )

    def restore_workflow_version(self, slug: str, version: int) -> StudioWorkflowView:
        """Apply an old template snapshot as a NEW head version. History is never
        mutated. Slug/source_path/built_in are identity fields and are preserved."""
        template = self._resolve_workflow_template(slug)
        if not template:
            raise ValueError(f"Workflow not found or unpublished: {slug}")
        row = self.session.exec(
            select(WorkflowTemplateVersion).where(
                WorkflowTemplateVersion.template_id == template.id,
                WorkflowTemplateVersion.version == version,
            )
        ).first()
        if not row:
            raise ValueError(f"Version {version} not found for workflow {slug}")
        snap = json.loads(row.snapshot_json or "{}")
        template.sqlmodel_update(
            {
                field: snap[field]
                for field in ("name", "description", "stages_json", "transitions_json")
                if field in snap
            }
        )
        template.version += 1
        self.session.add(template)
        self.session.flush()
        write_template_version(
            self.session, template, created_by="studio-ui", change_note=f"Restored from v{version}"
        )
        self.session.commit()
        self.session.refresh(template)
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if workflow:
            return _workflow_view(self.session, workflow)
        return _template_workflow_view(template)


def _auto_transitions(stages: list[StudioWorkflowStage]) -> list[dict[str, str]]:
    """Seed a linear forward chain for a workflow that has no routes of its own.

    Forward-only by construction: it cannot express `when: reject`. Use it to
    bootstrap, never to rewrite — see _prune_transitions and update_workflow.
    """
    ordered = sorted(stages, key=lambda stage: stage.order)
    transitions: list[dict[str, str]] = []
    for idx in range(len(ordered) - 1):
        transitions.append({"from": ordered[idx].key, "to": ordered[idx + 1].key})
    return transitions


def _prune_transitions(
    transitions: list[dict[str, str]],
    stages: list[StudioWorkflowStage],
) -> list[dict[str, str]]:
    """Drop routes whose endpoints no longer exist, preserving everything else.

    A route to a deleted stage is a phantom target: apply_stage_route can't resolve
    it, so it degrades to the previous-stage fallback rather than erroring.
    """
    keys = {stage.key for stage in stages}
    return [item for item in transitions if item.get("from") in keys and item.get("to") in keys]


def load_studio_agent_config(agent_id: str) -> dict | None:
    from loregarden.db.session import engine

    with Session(engine) as session:
        return studio_agent_config(session, agent_id)
