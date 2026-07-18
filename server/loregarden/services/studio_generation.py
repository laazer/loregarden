"""Studio generation: prompts for the agent/workflow authoring models and payload parsing."""

from __future__ import annotations

import re

from loregarden.models.domain import (
    ClassifyRoute,
    ParallelAgentSpec,
    StudioGeneratedAgent,
    StudioGeneratedWorkflow,
    StudioWorkflowStage,
    Workspace,
)
from loregarden.services.cli_agent_runner import CliAgentProfile, run_cli_agent_turn, stub_response
from loregarden.services.ticket_studio_service import extract_json_block
from sqlmodel import Session, select


def tool_names() -> list[str]:
    from loregarden.mcp.tools import tool_names

    return tool_names()


STUDIO_GENERATE_AGENT_ID = "planner"

MAX_STUDIO_GENERATE_CHARS = 4000

STUDIO_GENERATE_CLI_PROFILE = CliAgentProfile(
    agent_id=STUDIO_GENERATE_AGENT_ID,
    assistant_label="Studio generate assistant",
    cli_label="Studio generate",
    stub_env="LOREGARDEN_STUDIO_GENERATE_STUB_RESPONSE",
    timeout_env="LOREGARDEN_STUDIO_GENERATE_TIMEOUT",
    tmp_prefix="loregarden-studio-generate-",
    reply_cap=12000,
)

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


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "agent"


def _default_studio_workspace(session: Session) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    if not workspace:
        workspace = session.exec(select(Workspace).order_by(Workspace.slug)).first()
    if not workspace:
        raise ValueError("No workspace available for studio generation")
    return workspace


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
    stub = stub_response(STUDIO_GENERATE_CLI_PROFILE)
    if stub is not None:
        return stub

    return run_cli_agent_turn(
        STUDIO_GENERATE_CLI_PROFILE,
        workspace=_default_studio_workspace(session),
        prompt=prompt,
    )


def parse_agent_generate_payload(text: str) -> StudioGeneratedAgent | None:
    payload = extract_json_block(text)
    if not payload:
        return None
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    slug = slugify(str(payload.get("slug") or name))
    adapter = str(payload.get("adapter") or "claude").strip().lower()
    if adapter not in {"claude", "cursor", "lmstudio", "local"}:
        adapter = "claude"
    tools_raw = payload.get("mcp_tools") or []
    mcp_tools = [str(item).strip() for item in tools_raw if str(item).strip()]
    known_tools = set(tool_names())
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
    key = slugify(str(raw.get("key") or f"stage_{order}"))
    name = str(raw.get("name") or key.replace("-", " ").title()).strip()
    stage_type = str(raw.get("stage_type") or "agent").strip().lower()
    if stage_type not in {"agent", "classify", "gate", "parallel", "verify"}:
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
    parallel_agents: list[ParallelAgentSpec] = []
    for member_raw in raw.get("parallel_agents") or []:
        if not isinstance(member_raw, dict):
            continue
        member_agent = str(member_raw.get("agent_id") or "").strip()
        if member_agent and member_agent not in agent_ids:
            continue
        member_skill = str(member_raw.get("skill_name") or "").strip()
        if member_skill and member_skill not in skills:
            member_skill = ""
        if member_agent:
            parallel_agents.append(
                ParallelAgentSpec(agent_id=member_agent, skill_name=member_skill)
            )
    if stage_type == "parallel" and not parallel_agents:
        stage_type = "agent"
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
        parallel_agents=parallel_agents,
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
    slug = slugify(str(payload.get("slug") or name))
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
