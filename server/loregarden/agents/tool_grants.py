"""Turning an agent's configured tool grants into CLI flags — and into warnings.

Both outputs come from one derivation (``effective_allowlist``) on purpose. A
warning that disagreed with the argv would be worse than no warning at all: the
operator would be told their narrowing is safe while the subprocess enforces
something else.

The enforcement itself is Claude-only. Cursor, Codex and OpenCode have no
equivalent flag, so a grant configured on those adapters cannot take effect —
which is exactly the silent no-op this module refuses to ship, hence
``ToolGrantWarningCode.ADAPTER_IGNORES_GRANTS``.

Distinct from ``services.tool_policy`` and ``mcp.tool_ids``' auto-approve sets:
those decide whether a call runs unattended, this decides whether the tool is
offered at all. A tool withheld here never reaches the permission bridge, so it
leaves no approval row and no ``mcp_tool_calls`` telemetry — the reason grants
are derive-then-narrow rather than free-form, and the reason the default posture
is ``INHERIT``.
"""

from __future__ import annotations

import json
import logging

from loregarden.agents.cli_tool_ids import CHAT_ADVISORY_CLI_TOOLS, CHAT_INTERACTIVE_CLI_TOOLS
from loregarden.agents.mcp_context import CLAUDE_MCP_TOOL_PREFIX
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS
from loregarden.models.domain.enums import (
    ChatSurface,
    CliAdapter,
    ToolGrantWarningCode,
    ToolPosture,
)
from loregarden.models.domain.schemas import StudioAgentToolGrants, ToolGrantWarning

logger = logging.getLogger(__name__)

#: Wildcard suffix that grants every tool on a registered MCP server.
_SERVER_WILDCARD = "__*"


def parse_tool_grants(raw: str) -> StudioAgentToolGrants:
    """A stored ``tool_grants_json`` blob as its model, defaults for anything absent.

    A row written before grants existed holds ``{}`` and validates to the
    ``INHERIT`` default — the behaviour it already had.
    """
    return StudioAgentToolGrants.model_validate(json.loads(raw or "{}"))


def agent_tool_grants(agent: dict) -> StudioAgentToolGrants | None:
    """Grants from a resolved agent config, or ``None`` when it carries none.

    ``None`` and ``INHERIT`` both mean "do not constrain"; keeping them distinct
    lets a caller tell "this agent predates grants" from "this operator chose
    not to narrow", which the Studio UI shows differently.
    """
    grants = agent.get("tool_grants")
    if grants is None:
        return None
    return grants


def _mcp_tool_flag_names(mcp_tools: list[str]) -> set[str]:
    """The agent's Loregarden MCP grants, spelled as Claude names them."""
    return {f"{CLAUDE_MCP_TOOL_PREFIX}{name}" for name in mcp_tools if name}


def effective_allowlist(
    grants: StudioAgentToolGrants,
    *,
    mcp_tools: list[str],
    mcp_enabled: bool,
    surface: ChatSurface,
    interactive: bool = True,
) -> list[str]:
    """The tool names this agent may call, or an empty list for "don't constrain".

    Derive, then narrow. The set starts from what the rail offers and the agent's
    own MCP grants; ``allowed_tools`` can only subtract from it. That invariant is
    what keeps the allowlist a superset of anything the permission bridge could
    approve — an operator cannot accidentally widen access here, only shrink it.
    """
    if grants.posture is not ToolPosture.ALLOWLIST:
        return []

    base = CHAT_INTERACTIVE_CLI_TOOLS if interactive else CHAT_ADVISORY_CLI_TOOLS
    names = {tool.value for tool in base}
    if mcp_enabled:
        names |= _mcp_tool_flag_names(mcp_tools)
        names |= {f"mcp__{server}{_SERVER_WILDCARD}" for server in grants.mcp_servers if server}

    if grants.allowed_tools:
        keep = {tool.value for tool in grants.allowed_tools}
        # Narrow the CLI tools to the chosen ones; MCP entries are governed by
        # `mcp_tools` / `mcp_servers`, not by this CLI-tool selection, so a
        # narrowing that named only CLI tools must not silently drop MCP.
        names = {name for name in names if name.startswith("mcp__") or name in keep}

    names -= {tool.value for tool in grants.disallowed_tools}
    return sorted(names)


def claude_tool_flags(
    grants: StudioAgentToolGrants | None,
    *,
    mcp_tools: list[str],
    mcp_enabled: bool,
    surface: ChatSurface,
    interactive: bool = True,
    agent_slug: str = "",
) -> list[str]:
    """``--allowedTools`` / ``--disallowedTools`` argv fragments for Claude.

    The tool list is emitted as a **single comma-joined token**. Claude's flag is
    variadic (``--allowedTools <tools...>``), so space-separated values would
    swallow whatever positional followed them — including the user prompt.
    """
    if grants is None:
        return []
    allowlist = effective_allowlist(
        grants,
        mcp_tools=mcp_tools,
        mcp_enabled=mcp_enabled,
        surface=surface,
        interactive=interactive,
    )
    flags: list[str] = []
    if allowlist:
        flags.extend(["--allowedTools", ",".join(allowlist)])
    if grants.disallowed_tools:
        flags.extend(
            ["--disallowedTools", ",".join(sorted(tool.value for tool in grants.disallowed_tools))]
        )
    if flags:
        # A tool this list omits fails inside the model's turn and reaches no
        # telemetry table, so this line is the only trace an operator debugging
        # "why didn't Baxter call X" can find.
        logger.info(
            "tool grants applied (agent=%r surface=%s posture=%s allowed=%d disallowed=%d)",
            agent_slug or "?",
            surface.value,
            grants.posture.value,
            len(allowlist),
            len(grants.disallowed_tools),
        )
    return flags


def analyze_tool_grants(
    grants: StudioAgentToolGrants,
    *,
    adapter: str,
    mcp_tools: list[str],
    mcp_enabled: bool,
    registered_servers: frozenset[str],
    surface: ChatSurface = ChatSurface.HOME,
) -> list[ToolGrantWarning]:
    """Ways this configuration will not do what it looks like it does.

    Advisory: narrowing on purpose is legitimate, so nothing here blocks a save.
    Pure — ``registered_servers`` is passed in rather than looked up, so the
    caller resolves it once instead of once per agent in a list.
    """
    warnings: list[ToolGrantWarning] = []

    if grants.posture is ToolPosture.INHERIT:
        # The default. Nothing is configured, so nothing can fail to apply.
        return warnings

    if adapter != CliAdapter.CLAUDE:
        warnings.append(
            ToolGrantWarning(
                code=ToolGrantWarningCode.ADAPTER_IGNORES_GRANTS,
                message=(
                    f"Tool grants are configured, but this agent runs on {adapter}, which has "
                    "no tool-allowlist flag. These settings will have no effect until the "
                    "agent's provider is Claude."
                ),
            )
        )

    unknown = sorted(server for server in grants.mcp_servers if server not in registered_servers)
    if unknown:
        warnings.append(
            ToolGrantWarning(
                code=ToolGrantWarningCode.UNKNOWN_MCP_SERVER,
                message=(
                    "These MCP servers are not registered or not enabled, so granting them "
                    "matches nothing."
                ),
                tools=unknown,
            )
        )

    if grants.posture is not ToolPosture.ALLOWLIST:
        return warnings

    allowlist = effective_allowlist(
        grants, mcp_tools=mcp_tools, mcp_enabled=mcp_enabled, surface=surface
    )
    if not allowlist:
        warnings.append(
            ToolGrantWarning(
                code=ToolGrantWarningCode.EMPTY_ALLOWLIST,
                message=(
                    "This allowlist resolves to no tools at all — the agent could not even "
                    "read a file. Calls fail inside the turn with nothing in the approvals "
                    "inbox to explain why."
                ),
            )
        )
        return warnings

    granted = set(allowlist)
    if mcp_enabled and not any(name.startswith("mcp__") for name in granted):
        warnings.append(
            ToolGrantWarning(
                code=ToolGrantWarningCode.ALL_MCP_EXCLUDED,
                message=(
                    "No MCP tool survives this allowlist, so the agent cannot reach Loregarden "
                    "at all — it will answer about tickets and runs without being able to look "
                    "any of them up."
                ),
            )
        )

    missing_auto = sorted(
        f"{CLAUDE_MCP_TOOL_PREFIX}{tool.value}"
        for tool in AUTO_APPROVED_MCP_TOOLS
        if f"{CLAUDE_MCP_TOOL_PREFIX}{tool.value}" in _mcp_tool_flag_names(mcp_tools)
        and f"{CLAUDE_MCP_TOOL_PREFIX}{tool.value}" not in granted
    )
    if missing_auto:
        warnings.append(
            ToolGrantWarning(
                code=ToolGrantWarningCode.AUTO_APPROVED_EXCLUDED,
                message=(
                    "These tools would otherwise run without asking, and this allowlist puts "
                    "them out of reach. Calls to them fail silently inside the turn — no "
                    "approval request, no activity row."
                ),
                tools=missing_auto,
            )
        )
    return warnings
