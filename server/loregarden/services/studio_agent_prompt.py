"""The prompt a Studio agent run is given, and the editor's preview of it.

`studio_service.py` owns the Studio's CRUD; this module owns the one thing that
reads an agent row and renders it as *prompt text* — the config dict a run is
built from, the tool-guide/handoff/gate sections appended to every agent's role
body, and the preview the agent editor shows beside it. The run path
(`agents/registry.py`, `agents/executors/cli.py`) and the chat paths
(`triage_service`, `baxter_chat_service`) reach it here without importing the
CRUD service.
"""

from __future__ import annotations

import json
import logging

from loregarden.agents.mcp_context import select_transport_blocks
from loregarden.agents.tool_grants import parse_tool_grants
from loregarden.config import settings
from loregarden.models.domain import (
    ControlPlaneTransport,
    StudioAgent,
    StudioAgentPreview,
    StudioAgentPreviewProfile,
    StudioAgentPreviewRequest,
)
from loregarden.services.studio_agent_config import (
    default_mcp_tools,
    ensure_studio_role_preamble,
    frontmatter_bool,
    parse_markdown_frontmatter,
    resolve_studio_mcp_tools,
    strip_markdown_frontmatter,
)
from loregarden.services.studio_generation import tool_names
from loregarden.services.studio_mcp_tool_guides import MCP_TOOL_GUIDES
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


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


class _HandoffItem(BaseModel):
    """One stored handoff check, as the prompt renders it."""

    model_config = ConfigDict(extra="ignore")

    kind: str = "check"
    prompt: str = ""


class _GateItem(BaseModel):
    """One stored gate check, as the prompt renders it."""

    model_config = ConfigDict(extra="ignore")

    kind: str = "gate"
    title: str = ""
    impact: str = ""


def _checks(
    model: type[_HandoffItem] | type[_GateItem], raw: object, *, label: str
) -> list[_HandoffItem | _GateItem]:
    """The readable entries of a stored check list, modelled.

    `gate_checks_json` / `handoff_checks_json` are text blobs with no schema
    behind them, so an entry an older build or a hand-edit left in a shape this
    cannot read is skipped rather than rendered half-formed — and named at
    `warning`, because a check silently dropped out of an agent's prompt is a
    check nobody knows is missing.
    """
    items: list[_HandoffItem | _GateItem] = []
    for entry in TypeAdapter(list[object]).validate_python(raw or []):
        try:
            items.append(model.model_validate(entry))
        except ValidationError:
            logger.warning("%s: skipping an entry this build cannot read: %r", label, entry)
    return items


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
    handoffs = _checks(_HandoffItem, agent_cfg.get("handoff_checks"), label="handoff_checks")
    if handoffs:
        sections.append("## Handoff checks (required before stage completion)")
        sections.extend(f"- [{item.kind}] {item.prompt}" for item in handoffs)
    gates = _checks(_GateItem, agent_cfg.get("gate_checks"), label="gate_checks")
    if gates:
        sections.append("## Gate checks (human approval may be required)")
        sections.extend(f"- [{item.kind}] {item.title}: {item.impact}" for item in gates)
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


def load_studio_agent_config(agent_id: str) -> dict | None:
    from loregarden.db.session import engine

    with Session(engine) as session:
        return studio_agent_config(session, agent_id)
