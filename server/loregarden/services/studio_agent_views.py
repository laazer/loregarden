"""Rendering a studio agent — stored, historical, or registry fallback — for the API.

Split out of ``studio_service`` because these three builders share one shape and
one hazard: each needs ``registered_servers`` to compute tool-grant warnings, and
each takes it as a **required keyword**. A default would let a caller that forgot
to resolve the registry report "no warnings" — indistinguishable from a genuinely
clean configuration, which is the class of silent failure the grants feature
exists to remove.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loregarden.agents.tool_grants import analyze_tool_grants, parse_tool_grants
from loregarden.models.domain import (
    StudioAgent,
    StudioAgentToolGrants,
    StudioAgentView,
    StudioGateCheck,
    StudioHandoffCheck,
)
from loregarden.services.studio_agent_config import (
    ensure_studio_role_preamble,
    load_role_body,
    parse_json_list,
    resolve_studio_mcp_tools,
)
from loregarden.services.studio_generation import tool_names


def agent_view(agent: StudioAgent, *, registered_servers: frozenset[str]) -> StudioAgentView:
    """Render a stored agent for the API.

    ``registered_servers`` is required and keyword-only on purpose. The warnings
    below need it, and giving it a default would make "the caller forgot to
    resolve it" indistinguishable from "no servers are registered" — a wrong
    warning list that looks exactly like a correct one. Resolve it once at the
    public boundary (see ``registered_mcp_server_names``) and pass it down;
    listing agents must not re-query per row.
    """
    raw_tools = json.loads(agent.mcp_tools_json or "[]")
    tools = resolve_studio_mcp_tools(raw_tools, mcp_enabled=agent.mcp_enabled)
    grants = parse_tool_grants(agent.tool_grants_json)
    return StudioAgentView(
        id=agent.id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        role_body=ensure_studio_role_preamble(agent.role_body),
        role_file="",
        adapter=agent.adapter,
        default_model=agent.default_model,
        timeout=agent.timeout,
        default_skill=agent.default_skill,
        mcp_enabled=agent.mcp_enabled,
        mcp_tools=tools,
        gate_checks=parse_json_list(agent.gate_checks_json, StudioGateCheck),
        handoff_checks=parse_json_list(agent.handoff_checks_json, StudioHandoffCheck),
        tool_grants=grants,
        tool_grant_warnings=analyze_tool_grants(
            grants,
            adapter=agent.adapter,
            mcp_tools=tools,
            mcp_enabled=agent.mcp_enabled,
            registered_servers=registered_servers,
        ),
        built_in=agent.built_in,
        read_only=False,
        version=agent.version,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def agent_snapshot_view(
    agent: StudioAgent, snap: dict, *, registered_servers: frozenset[str]
) -> StudioAgentView:
    """Render a historical agent snapshot (read-only) for the version-detail view."""
    mcp_enabled = bool(snap.get("mcp_enabled", True))
    raw_tools = json.loads(snap.get("mcp_tools_json") or "[]")
    snapshot_tools = resolve_studio_mcp_tools(raw_tools, mcp_enabled=mcp_enabled)
    snapshot_grants = parse_tool_grants(snap.get("tool_grants_json") or "{}")
    return StudioAgentView(
        id=agent.id,
        slug=snap.get("slug", agent.slug),
        name=snap.get("name", ""),
        description=snap.get("description", ""),
        role_body=ensure_studio_role_preamble(snap.get("role_body", "")),
        role_file="",
        adapter=snap.get("adapter", "claude"),
        default_model=snap.get("default_model", ""),
        timeout=int(snap.get("timeout", 600)),
        default_skill=snap.get("default_skill", ""),
        mcp_enabled=mcp_enabled,
        mcp_tools=snapshot_tools,
        gate_checks=parse_json_list(snap.get("gate_checks_json", "[]"), StudioGateCheck),
        handoff_checks=parse_json_list(snap.get("handoff_checks_json", "[]"), StudioHandoffCheck),
        tool_grants=snapshot_grants,
        tool_grant_warnings=analyze_tool_grants(
            snapshot_grants,
            adapter=snap.get("adapter", "claude"),
            mcp_tools=snapshot_tools,
            mcp_enabled=mcp_enabled,
            registered_servers=registered_servers,
        ),
        built_in=bool(snap.get("built_in", False)),
        read_only=True,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def builtin_agent_view(
    agent_id: str, cfg: dict, *, registered_servers: frozenset[str]
) -> StudioAgentView:
    now = datetime.now(timezone.utc)
    role_file = str(cfg.get("role_file", ""))
    role_body, excerpt = load_role_body(role_file)
    # An un-seeded registry fallback has no stored grants, so it carries the
    # default posture and cannot warn about anything. The parameter stays
    # required so this view keeps the same shape as the others.
    _ = registered_servers
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
        tool_grants=StudioAgentToolGrants(),
        tool_grant_warnings=[],
        built_in=True,
        read_only=True,
        created_at=now,
        updated_at=now,
    )
