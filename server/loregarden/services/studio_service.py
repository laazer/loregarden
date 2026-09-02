"""Agent & Workflow Studio — custom agents and workflow definitions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.agents.mcp_context import select_transport_blocks
from loregarden.agents.registry import AGENTS
from loregarden.agents.registry import list_agents as list_builtin_agents
from loregarden.agents.tool_grants import parse_tool_grants
from loregarden.config import settings
from loregarden.core.workflow_loader import write_template_version
from loregarden.mcp.tool_ids import (
    MEMORY_DEFAULT_MCP_TOOLS,
    mcp_tool_values,
)
from loregarden.models.domain import (
    ControlPlaneTransport,
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
    StudioSkillCreate,
    StudioSkillRestore,
    StudioSkillUpdate,
    StudioSkillVersionView,
    StudioSkillView,
    StudioWorkflow,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    StudioWorkflowUpdate,
    StudioWorkflowVersionView,
    StudioWorkflowView,
    WorkflowTemplate,
    WorkflowTemplateVersion,
)
from loregarden.services.mcp_registry import registered_mcp_server_names
from loregarden.services.skill_service import SkillService
from loregarden.services.studio_agent_config import (
    default_mcp_tools,
    ensure_studio_role_preamble,
    frontmatter_bool,
    load_role_body,
    parse_markdown_frontmatter,
    resolve_studio_mcp_tools,
    strip_markdown_frontmatter,
)
from loregarden.services.studio_agent_views import (
    agent_snapshot_view,
    agent_view,
    builtin_agent_view,
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
from loregarden.services.studio_routing import SKIP_CONDITIONS, TERMINAL_STAGE_KEY
from loregarden.services.workflow_service import WorkflowService
from loregarden.skills.registry import list_skills
from sqlmodel import Session, select

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
        example='tools/call loregarden_get_ticket {"ticket_id": "<uuid or external id>", "workspace_slug": "loregarden"}',
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
        when_to_use="When you know the ticket id (e.g. lor-mcp-gateway-142) but not the UUID.",
        example='tools/call loregarden_get_ticket_by_external {"workspace_slug": "loregarden", "external_id": "lor-mcp-gateway-142"}',
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
        when_to_use=(
            "When a stage the template marks optional does not apply to this ticket — a "
            "frontend review on a backend-only change. Required stages are refused."
        ),
        example='tools/call loregarden_skip_stage {"run_id": "<orch run id>", "stage_key": "frontend_review", "reason": "No client/ files touched"}',
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
    StudioMcpToolGuide(
        name="loregarden_search_reference",
        description="Search DevDocs for a documentation page and get back ranked entries with their URLs.",
        when_to_use="Before fetching framework docs — search for the exact page instead of guessing a URL, then pass the returned url to loregarden_fetch_reference. Omit docset to get suggestions.",
        example='tools/call loregarden_search_reference {"query": "useEffect", "docset": "react"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_fetch_reference",
        description="Fetch a documentation page through loregarden's cache, extracted to markdown.",
        when_to_use="Instead of WebFetch for framework or library docs — the raw HTML is fetched and extracted once per URL, and repeat reads are served from the cache.",
        example='tools/call loregarden_fetch_reference {"url": "https://docs.pydantic.dev/latest/concepts/models/"}',
        stage_agent=True,
    ),
]


logger = logging.getLogger(__name__)

# Fields captured verbatim in each StudioAgentVersion snapshot. Matches the
# migration backfill (0022) for the columns that existed then; later columns are
# snapshotted going forward only, and a restore of an older snapshot resets them
# to their default rather than carrying the current value forward (see
# `restore_agent_version`).
#
# Together with _SNAPSHOT_EXCLUDED_FIELDS this must cover every column on
# StudioAgent — test_studio_agent_snapshot asserts the partition, so a new
# column fails the build until someone decides which side it belongs on. Left to
# memory, an omission here loses that column on every restore with no error.
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
    "tool_grants_json",
    "built_in",
)

# Identity and bookkeeping — deliberately NOT snapshotted. `id` identifies the
# row a restore writes back to; `version` / `created_at` / `updated_at` are the
# history's own metadata, and restoring them would rewrite the history instead
# of appending to it.
_SNAPSHOT_EXCLUDED_FIELDS = ("id", "version", "created_at", "updated_at")


def _agent_snapshot(agent: StudioAgent) -> dict:
    return agent.model_dump(include=set(_AGENT_SNAPSHOT_FIELDS))


#: ``StudioAgentUpdate`` field -> ``StudioAgent`` column. A table rather than a
#: chain of ``if`` statements: each new editable field was another branch, and
#: the branch count is what the complexity gate measures. Adding a field is one
#: row here, not one branch in the updater.
_AGENT_UPDATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "name"),
    ("description", "description"),
    ("role_body", "role_body"),
    ("adapter", "adapter"),
    ("default_model", "default_model"),
    ("timeout", "timeout"),
    ("default_skill", "default_skill"),
    ("mcp_enabled", "mcp_enabled"),
    ("mcp_tools", "mcp_tools_json"),
    ("gate_checks", "gate_checks_json"),
    ("handoff_checks", "handoff_checks_json"),
    ("tool_grants", "tool_grants_json"),
)

#: Columns stored as a JSON blob. ``model_dump`` has already turned any nested
#: model into plain data by the time these are written, so one dumps() covers
#: lists of checks and the grants object alike.
_AGENT_JSON_COLUMNS = frozenset(
    {"mcp_tools_json", "gate_checks_json", "handoff_checks_json", "tool_grants_json"}
)

_AGENT_STRIPPED_COLUMNS = frozenset({"name", "description"})


def _agent_update_values(body: StudioAgentUpdate) -> dict[str, object]:
    """Column -> value for the fields this update actually supplied.

    ``exclude_none`` is the "field omitted" test: a PATCH that leaves a field out
    must not overwrite it. ``change_note`` is absent from the table on purpose —
    it belongs to the version entry, not the agent row.
    """
    provided = body.model_dump(exclude_none=True)
    values: dict[str, object] = {}
    for field, column in _AGENT_UPDATE_COLUMNS:
        if field not in provided:
            continue
        value = provided[field]
        if column in _AGENT_JSON_COLUMNS:
            values[column] = json.dumps(value)
        elif column in _AGENT_STRIPPED_COLUMNS:
            values[column] = str(value).strip()
        elif column == "role_body":
            values[column] = ensure_studio_role_preamble(str(value))
        else:
            values[column] = value
    return values


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
    # Distinguishes "this root has no agent assets" from "an asset it declares
    # is missing" — see the role-body guard below.
    has_agent_context = settings.agent_context_dir.is_dir()
    seeded: list[str] = []
    for slug, cfg in AGENTS.items():
        if slug in existing:
            continue
        role_file = str(cfg.get("role_file", ""))
        role_body, excerpt = load_role_body(role_file)
        if role_file and not role_body:
            # The role body is what a chat rail renders as its identity, so an
            # empty seed produces an agent that answers with no character and no
            # rules — indistinguishable, from the outside, from a working one.
            #
            # A *missing agent_context tree* is a different situation and an
            # expected one: initialising a database for a root that has no
            # agent assets yet (`init_db` does this, and says so for skills
            # too). There is nothing to read, so seed the row and warn.
            # A tree that exists but lacks a role file its own registry
            # declares is a broken checkout — fail rather than ship the
            # lobotomised agent.
            if has_agent_context:
                raise ValueError(
                    f"Built-in agent {slug!r} declares role_file {role_file!r}, but it is "
                    f"missing or empty under {settings.agent_context_dir}. Seeding it would "
                    "create an agent with no role instructions."
                )
            logger.warning(
                "seed_builtin_agents: no agent_context tree at %s; seeding %r with an empty "
                "role body. Chat rails will render no role for it until the assets exist.",
                settings.agent_context_dir,
                slug,
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
        "slug": agent.slug,
        "tool_grants": parse_tool_grants(agent.tool_grants_json),
        "name": agent.name,
        "role_body": ensure_studio_role_preamble(agent.role_body),
        "adapter": agent.adapter,
        "default_model": agent.default_model,
        "timeout": agent.timeout,
        "default_skill": agent.default_skill,
        "mcp_enabled": agent.mcp_enabled,
        "mcp_tools": resolve_studio_mcp_tools(raw_tools, mcp_enabled=agent.mcp_enabled),
        "gate_checks": json.loads(agent.gate_checks_json or "[]"),
        "handoff_checks": json.loads(agent.handoff_checks_json or "[]"),
        "studio": True,
    }


def build_studio_prompt_sections(
    agent_cfg: dict, *, transport: ControlPlaneTransport = ControlPlaneTransport.MCP
) -> str:
    """This agent's own tool guide, handoff checks and gates.

    ``transport`` decides how the tools are described, because "use these MCP
    tools" is false for a run that has none. The pointers to the MCP and memory
    modules that used to live here are gone: every consumer of this block
    already appends both modules a few lines further down.
    """
    sections: list[str] = []
    if agent_cfg.get("mcp_enabled", True):
        tools = agent_cfg.get("mcp_tools") or tool_names()
        lead = (
            "Use these MCP tools for ticket workflow state:"
            if transport is ControlPlaneTransport.MCP
            else "Use these tools for ticket workflow state, each invoked as "
            "`./scripts/loregarden-cli.sh mcp call <tool> key=value…`:"
        )
        sections.extend(["## Loregarden control-plane tools", lead, ", ".join(tools)])
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
        "role_body": ensure_studio_role_preamble(body.role_body),
        "adapter": body.adapter,
        "timeout": body.timeout,
        "default_skill": body.default_skill,
        "mcp_enabled": body.mcp_enabled,
        "mcp_tools": resolve_studio_mcp_tools(tools, mcp_enabled=body.mcp_enabled),
        "gate_checks": [item.model_dump() for item in body.gate_checks],
        "handoff_checks": [item.model_dump() for item in body.handoff_checks],
    }


def preview_agent_markdown(body: StudioAgentPreviewRequest) -> StudioAgentPreview:
    cfg = _preview_agent_cfg(body)
    role_frontmatter = parse_markdown_frontmatter(body.role_body)
    role_body = ensure_studio_role_preamble(strip_markdown_frontmatter(body.role_body)).strip()
    metadata = StudioAgentPreviewProfile(
        description=role_frontmatter.get("description") or body.description.strip(),
        model=role_frontmatter.get("model") or "",
        provider=body.adapter or "claude",
        default_skill=body.default_skill or "",
        timeout=body.timeout,
        always_apply=frontmatter_bool(role_frontmatter.get("alwaysApply")),
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
        # The preview shows a supervised run's prompt, and a supervised run is
        # the one this process wires MCP into — so the module is rendered for
        # that transport rather than with both channels' text at once.
        mcp_doc = select_transport_blocks(
            strip_markdown_frontmatter(mcp_doc_path.read_text(encoding="utf-8")),
            ControlPlaneTransport.MCP,
        )[:8000]
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


def _validate_classify_routes_are_selectable(stages: list[StudioWorkflowStage]) -> None:
    """Reject a classify route nothing can choose on purpose.

    `_select_classify_route` picks a route one of three ways: content scoring on
    `specialties`/`languages`, the pin when it names exactly one route, or the
    route marked `default`. A route with no specialties, no languages and no
    default flag is reachable by none of them — it can only be reached by being
    first in the list, which is position, not intent. The author almost
    certainly meant it to be the default.

    This is a forward guard, not a fix for anything shipped: every classify route
    in every live template is currently selectable. It is here because the defect
    it prevents is the neighbour of one that did ship — a pin matching on agent
    picked `classify_routes[0]` for 471 tickets, and "whichever is first" is the
    same failure wearing different clothes.

    Deliberately NOT rejected: two routes naming the same agent with different
    branches. `studio-loregarden-tdd-v3`'s triage stage does exactly that — the
    scoper triages everything, and typo/docs work skips ahead to `test-design` —
    and it is a reasonable thing to express. The pin cannot disambiguate it, so
    `_select_classify_route` declines to let the pin steer there at all; the
    template is fine, and forbidding it would remove a working shortcut.
    """
    unreachable = sorted(
        {
            f"{stage.key}[{index}] -> {route.agent_id or '(no agent)'}"
            for stage in stages
            for index, route in enumerate(stage.classify_routes or [])
            if not (route.specialties or route.languages or route.default)
        }
    )
    if unreachable:
        raise ValueError(
            "Classify route(s) can only be chosen by list position, which is not a "
            f"routing rule: {', '.join(unreachable)}. Give each one specialties or "
            "languages to match on, or mark one `default`."
        )


def _validate_has_terminal_stage(stages: list[StudioWorkflowStage]) -> None:
    """Reject a workflow with no terminal stage. Without one the orchestrator has
    nowhere to finalize on: a passing final stage re-loops instead of completing
    the ticket (the studio-loregarden-tdd v2/v3 templates shipped this way and
    cycled back to implement after the gate passed). A stage is terminal via the
    `terminal` flag or the historical `done` key — matching is_terminal_stage.
    """
    if stages and not any(stage.terminal or stage.key == TERMINAL_STAGE_KEY for stage in stages):
        raise ValueError(
            "Workflow must have a terminal stage (set `terminal: true`, or add a "
            "`done` stage) so the orchestrator can finalize the ticket."
        )


def _available_skills() -> list[str]:
    return list_skills()


def _collect_stage_skill_names(stages: list[StudioWorkflowStage]) -> set[str]:
    names: set[str] = set()
    for stage in stages:
        if stage.skill_name:
            names.add(stage.skill_name)
        for route in stage.classify_routes or []:
            if route.skill_name:
                names.add(route.skill_name)
        for spec in stage.parallel_agents or []:
            if spec.skill_name:
                names.add(spec.skill_name)
    return names


def _validate_stage_skill_names(stages: list[StudioWorkflowStage]) -> None:
    """Reject a workflow whose stages declare skills that do not exist.

    The mirror of _validate_stage_agent_ids, and missing for the same reason it
    was: nothing checked skills on the way in, so a dangling name saved cleanly
    and raised SkillNotFoundError at prompt-build time instead — a run that dies
    several steps from the edit that caused it.
    """
    unknown = sorted(_collect_stage_skill_names(stages) - set(_available_skills()))
    if unknown:
        raise ValueError(f"Workflow references unknown skill(s): {', '.join(unknown)}")


def _validate_default_skill(default_skill: str) -> None:
    """Reject an agent whose default skill does not exist. Every stage that
    dispatches the agent would otherwise fail at render, not at save."""
    if default_skill and default_skill not in set(_available_skills()):
        raise ValueError(f"Agent references unknown skill: {default_skill}")


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
            "memory_mcp_tools": mcp_tool_values(MEMORY_DEFAULT_MCP_TOOLS),
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
        generated = parse_agent_generate_payload(reply, skills=_available_skills())
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

    def _registered_servers(self) -> frozenset[str]:
        """The registry read every agent view needs, done once per operation."""
        return registered_mcp_server_names(self.session)

    def _agent_view_with_warnings(self, agent: StudioAgent) -> StudioAgentView:
        return agent_view(agent, registered_servers=self._registered_servers())

    def list_agents(self, *, include_builtin: bool = True) -> list[StudioAgentView]:
        # Resolved once for the whole list rather than per row — the warnings
        # need it, and a per-agent lookup would turn one query into N.
        registered = self._registered_servers()
        custom = [
            agent_view(agent, registered_servers=registered)
            for agent in self.session.exec(select(StudioAgent).order_by(StudioAgent.name)).all()
        ]
        if not include_builtin:
            return custom
        builtin_ids = {item.slug for item in custom}
        merged = list(custom)
        for item in list_builtin_agents():
            if item["id"] in builtin_ids:
                continue
            merged.append(builtin_agent_view(item["id"], item, registered_servers=registered))
        return sorted(merged, key=lambda item: (not item.built_in, item.name.lower()))

    def get_agent(self, slug: str) -> StudioAgentView | None:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if agent:
            return self._agent_view_with_warnings(agent)
        cfg = AGENTS.get(slug)
        if cfg:
            return builtin_agent_view(slug, cfg, registered_servers=self._registered_servers())
        return None

    def create_agent(self, body: StudioAgentCreate) -> StudioAgentView:
        slug = slugify(body.slug)
        if self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first():
            raise ValueError(f"Studio agent already exists: {slug}")
        if slug in AGENTS:
            raise ValueError(f"Slug conflicts with built-in agent: {slug}")
        _validate_default_skill(body.default_skill)
        now = datetime.now(timezone.utc)
        mcp_tools = body.mcp_tools if body.mcp_tools else default_mcp_tools()
        handoffs = body.handoff_checks if body.handoff_checks else DEFAULT_HANDOFF_CHECKS
        agent = StudioAgent(
            slug=slug,
            name=body.name.strip(),
            description=body.description.strip(),
            role_body=ensure_studio_role_preamble(body.role_body),
            adapter=body.adapter or "claude",
            default_model=body.default_model,
            timeout=body.timeout,
            default_skill=body.default_skill,
            mcp_enabled=body.mcp_enabled,
            mcp_tools_json=json.dumps(mcp_tools),
            gate_checks_json=json.dumps([item.model_dump() for item in body.gate_checks]),
            handoff_checks_json=json.dumps([item.model_dump() for item in handoffs]),
            tool_grants_json=body.tool_grants.model_dump_json(),
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
        return self._agent_view_with_warnings(agent)

    def update_agent(self, slug: str, body: StudioAgentUpdate) -> StudioAgentView:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        if body.default_skill is not None:
            _validate_default_skill(body.default_skill)
        agent.sqlmodel_update(_agent_update_values(body))
        agent.updated_at = datetime.now(timezone.utc)
        agent.version += 1
        self.session.add(agent)
        self.session.flush()
        _write_agent_version(
            self.session, agent, created_by="studio-ui", change_note=body.change_note or ""
        )
        self.session.commit()
        self.session.refresh(agent)
        return self._agent_view_with_warnings(agent)

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

    def list_skills(self) -> list[StudioSkillView]:
        return SkillService(self.session).list_skills()

    def get_skill(self, slug: str) -> StudioSkillView | None:
        return SkillService(self.session).get_skill(slug)

    def create_skill(self, body: StudioSkillCreate) -> StudioSkillView:
        return SkillService(self.session).create_skill(body)

    def update_skill(self, slug: str, body: StudioSkillUpdate) -> StudioSkillView:
        return SkillService(self.session).update_skill(slug, body)

    def list_skill_versions(self, slug: str) -> list[StudioSkillVersionView]:
        return SkillService(self.session).list_skill_versions(slug)

    def get_skill_version(self, slug: str, version: int) -> StudioSkillVersionView:
        return SkillService(self.session).get_skill_version(slug, version)

    def restore_skill_version(
        self, slug: str, version: int, body: StudioSkillRestore | None = None
    ) -> StudioSkillView:
        return SkillService(self.session).restore_skill_version(slug, version, body)

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
            snapshot=agent_snapshot_view(
                agent, snap, registered_servers=self._registered_servers()
            ),
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
        # A snapshot older than a column simply lacks its key. Carrying the
        # current value forward would leave a "restored" agent silently holding
        # settings that version never had — for tool grants that means a policy
        # outliving the restore that was supposed to undo it. Reset to the
        # column default instead, and report which fields that touched.
        # Read defaults off the field definitions rather than an instance: a
        # required column (``name``) is simply unset on a bare StudioAgent, so
        # instantiating one and dumping it silently omits those keys.
        defaults = {
            field: StudioAgent.model_fields[field].get_default(call_default_factory=True)
            for field in _AGENT_SNAPSHOT_FIELDS
        }
        current = agent.model_dump()
        reset_fields = [
            field
            for field in _AGENT_SNAPSHOT_FIELDS
            if field != "slug" and field not in snap and current[field] != defaults[field]
        ]
        restored = {
            field: snap.get(field, defaults[field])
            for field in _AGENT_SNAPSHOT_FIELDS
            if field != "slug"
        }
        if reset_fields:
            logger.info(
                "restore of agent %r to v%s predates %s; reset to defaults",
                slug,
                version,
                ", ".join(reset_fields),
            )
        agent.sqlmodel_update(restored)
        agent.version += 1
        agent.updated_at = datetime.now(timezone.utc)
        self.session.add(agent)
        self.session.flush()
        note = f"Restored from v{version}"
        if reset_fields:
            # Carried in the change note rather than a log line alone: the
            # version history is where an operator looks to understand what a
            # restore did, and a settings reset they cannot see is the kind of
            # quiet change that gets discovered weeks later.
            note += f" (v{version} predates {', '.join(reset_fields)}; reset to defaults)"
        _write_agent_version(self.session, agent, created_by="studio-ui", change_note=note)
        self.session.commit()
        self.session.refresh(agent)
        return self._agent_view_with_warnings(agent)

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
        _validate_classify_routes_are_selectable(stages)
        _validate_has_terminal_stage(stages)
        _validate_stage_skill_names(stages)
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
            _validate_classify_routes_are_selectable(stages)
            _validate_has_terminal_stage(stages)
            _validate_stage_skill_names(stages)
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
        _validate_classify_routes_are_selectable(stages)
        _validate_has_terminal_stage(stages)
        _validate_stage_skill_names(stages)

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
                    # `terminal` and `skip_when` were dropped here, so a stage's
                    # terminal marker (and its skip condition) never survived a
                    # publish — the published template could not finalize.
                    "terminal": stage.terminal,
                    "skip_when": stage.skip_when,
                    "classify_routes": [route.model_dump() for route in stage.classify_routes],
                    "parallel_agents": [item.model_dump() for item in stage.parallel_agents],
                    "gate_commands": list(stage.gate_commands),
                    "gate_required": stage.gate_required,
                    "model": stage.model,
                    # Same lesson as `terminal` / `skip_when` above: a field left
                    # out here is dropped from the published template. These three
                    # are live — `required_evidence` is what makes the implement
                    # and verify stages prove their work.
                    "required_evidence": list(stage.required_evidence),
                    "checklist": list(stage.checklist),
                    "stage_brief": stage.stage_brief,
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
