"""Agent & Workflow Studio — custom agents and workflow definitions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from loregarden.agents.registry import AGENTS
from loregarden.agents.registry import list_agents as list_builtin_agents
from loregarden.config import settings
from loregarden.models.domain import (
    ClassifyRoute,
    StudioAgent,
    StudioAgentCreate,
    StudioAgentPreview,
    StudioAgentPreviewRequest,
    StudioAgentUpdate,
    StudioAgentView,
    StudioGateCheck,
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
)
from loregarden.services.workflow_service import WorkflowService
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
]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "agent"


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
    return StudioAgentView(
        id=agent.id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        role_body=agent.role_body,
        role_file="",
        adapter=agent.adapter,
        timeout=agent.timeout,
        default_skill=agent.default_skill,
        mcp_enabled=agent.mcp_enabled,
        mcp_tools=json.loads(agent.mcp_tools_json or "[]"),
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
    enabled_tools = json.loads(agent.mcp_tools_json or "[]")
    return {
        "name": agent.name,
        "role_body": agent.role_body,
        "adapter": agent.adapter,
        "timeout": agent.timeout,
        "default_skill": agent.default_skill,
        "mcp_enabled": agent.mcp_enabled,
        "mcp_tools": enabled_tools or _tool_names(),
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
    tools = body.mcp_tools or (DEFAULT_STAGE_MCP_TOOLS if body.mcp_enabled else [])
    return {
        "name": body.name,
        "role_body": body.role_body,
        "adapter": body.adapter,
        "timeout": body.timeout,
        "default_skill": body.default_skill,
        "mcp_enabled": body.mcp_enabled,
        "mcp_tools": tools,
        "gate_checks": [item.model_dump() for item in body.gate_checks],
        "handoff_checks": [item.model_dump() for item in body.handoff_checks],
    }


def preview_agent_markdown(body: StudioAgentPreviewRequest) -> StudioAgentPreview:
    cfg = _preview_agent_cfg(body)
    section_names: list[str] = ["header", "role"]
    parts = [
        f"# Agent: {body.name}",
        "",
        body.description.strip() or "_No description provided._",
        "",
        "## Agent Role",
        body.role_body.strip() or "_No role instructions yet._",
    ]
    studio_sections = build_studio_prompt_sections(cfg)
    if studio_sections:
        section_names.extend(["mcp_tools", "handoffs", "gates"])
        parts.extend(["", studio_sections])
    mcp_doc_path = settings.agent_context_dir / "agents/common_assets/loregarden_mcp_v1.md"
    if body.mcp_enabled and mcp_doc_path.is_file():
        section_names.append("mcp_module")
        mcp_doc = mcp_doc_path.read_text(encoding="utf-8")[:8000]
        parts.extend(["", "## Loregarden MCP module (excerpt)", mcp_doc])
    parts.extend(
        [
            "",
            "## Permission policy",
            "Request human approval via Loregarden before destructive or high-risk tool use.",
            "Do not bypass workspace permission checks.",
        ]
    )
    section_names.append("permissions")
    return StudioAgentPreview(markdown="\n".join(parts), sections=section_names)


def resolve_classify_route(ticket: Ticket, stage: WorkflowStageDef) -> tuple[str, str]:
    if stage.stage_type != "classify" or not stage.classify_routes:
        return stage.agent_id, stage.skill_name

    routed = _resolve_next_agent_from_routes(ticket, stage)
    if routed:
        return routed

    haystack = " ".join(
        [
            ticket.title or "",
            ticket.description or "",
            ticket.external_id or "",
        ]
    ).lower()

    default_route: ClassifyRoute | None = None
    for route in stage.classify_routes:
        if route.default:
            default_route = route
            continue
        lang_ok = not route.languages or any(lang.lower() in haystack for lang in route.languages)
        spec_ok = not route.specialties or any(
            spec.lower() in haystack for spec in route.specialties
        )
        if lang_ok and spec_ok:
            return route.agent_id, route.skill_name or stage.skill_name

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
            "mcp_tools": DEFAULT_STAGE_MCP_TOOLS,
            "handoff_checks": [item.model_dump() for item in DEFAULT_HANDOFF_CHECKS],
            "gate_checks": [item.model_dump() for item in DEFAULT_GATE_CHECKS],
        }

    def preview_agent(self, body: StudioAgentPreviewRequest) -> StudioAgentPreview:
        return preview_agent_markdown(body)

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
        mcp_tools = body.mcp_tools if body.mcp_tools else DEFAULT_STAGE_MCP_TOOLS
        handoffs = body.handoff_checks if body.handoff_checks else DEFAULT_HANDOFF_CHECKS
        agent = StudioAgent(
            slug=slug,
            name=body.name.strip(),
            description=body.description.strip(),
            role_body=body.role_body,
            adapter=body.adapter or "claude",
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
            agent.role_body = body.role_body
        if body.adapter is not None:
            agent.adapter = body.adapter
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
