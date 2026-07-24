"""Access-control tests for the interim `loregarden_create_ticket` allowlist.

Per the ticket's triage checkpoint (a9-create-ticket-mcp-tool): orchestrated pipeline
agents (implementer, reviewer, test_designer, etc. — any agent run driven through
`PermissionBridgeRunner`, whether kicked off by the builtin autopilot or a single
manually-started stage) must be DENIED `loregarden_create_ticket` by default; an
implementer spawning tickets mid-run is scope-creep and noise. Interactive/copilot
contexts (Ticket Studio chat, a human's own terminal Claude Code session, direct
operator MCP/HTTP calls) are ALLOWED. This is recorded as debt, superseded once
a2-per-agent-server-policy's real per-agent x per-server policy table lands.

These tests pin the *design proposal* recorded in this ticket's test-design checkpoint
for where that interim gate lives — a new pure predicate in permission_bridge.py,
mirroring the existing `is_auto_approved_mcp_tool` convention exactly, backed by a new
frozenset. If the implementer picks a different shape, update these tests in the same
change (compatibility posture for this ticket is `internal` — interfaces may be broken
freely as long as every caller/test is migrated together).

Expected to fail red (ImportError) until permission_bridge.py grows:
    ORCHESTRATED_DENIED_MCP_TOOLS: frozenset[str]
    is_orchestrated_agent_denied_mcp_tool(tool_name: str) -> bool
"""

from __future__ import annotations

import pytest
from loregarden.agents.executors.permission_bridge import AUTO_APPROVED_MCP_TOOLS
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments


def test_create_ticket_is_registered_but_never_auto_approved():
    """Baseline safety property, true regardless of the exact gating mechanism:
    a tool an orchestrated agent must be denied can never be silently
    auto-approved for anyone."""
    assert "loregarden_create_ticket" not in AUTO_APPROVED_MCP_TOOLS


def test_create_ticket_is_denied_for_orchestrated_agents():
    from loregarden.agents.executors.permission_bridge import (
        ORCHESTRATED_DENIED_MCP_TOOLS,
        is_orchestrated_agent_denied_mcp_tool,
    )

    assert "loregarden_create_ticket" in ORCHESTRATED_DENIED_MCP_TOOLS
    assert (
        is_orchestrated_agent_denied_mcp_tool("mcp__loregarden__loregarden_create_ticket") is True
    )
    assert is_orchestrated_agent_denied_mcp_tool("loregarden_create_ticket") is True


def test_read_only_and_bookkeeping_tools_are_not_denied_for_orchestrated_agents():
    """The interim allowlist must be a narrow, additive carve-out — it must not
    regress any tool orchestrated agents already rely on."""
    from loregarden.agents.executors.permission_bridge import (
        is_orchestrated_agent_denied_mcp_tool,
    )

    for tool in (
        "loregarden_get_ticket",
        "loregarden_update_ticket",
        "loregarden_attach_artifact",
        "loregarden_complete_stage",
        "loregarden_write_handoff",
    ):
        assert is_orchestrated_agent_denied_mcp_tool(f"mcp__loregarden__{tool}") is False, tool


# --- real dispatch entrypoint ----------------------------------------------
#
# The predicate above is necessary but not sufficient: `PermissionBridgeRunner`
# (where `_try_fast_approve` consults it) is only reached via the CLI-subprocess
# permission-request stream. A direct HTTP `/mcp` call or an `external_mcp`-driven
# orchestrator calls `mcp.tools.execute_tool` directly and skips that runner
# entirely — these tests pin the second, independent check that closes that gap.


def test_execute_tool_denies_create_ticket_when_orchestrated(db_session):
    args = normalize_tool_arguments(
        "loregarden_create_ticket",
        {
            "workspace_slug": "loregarden",
            "title": "Should not be created",
            "work_item_type": "task",
        },
    )
    with pytest.raises(ValueError, match="(?i)orchestrated"):
        execute_tool(db_session, "loregarden_create_ticket", args, orchestrated=True)


def test_execute_tool_allows_create_ticket_when_not_orchestrated(db_session):
    args = normalize_tool_arguments(
        "loregarden_create_ticket",
        {
            "workspace_slug": "loregarden",
            "title": "Interactive call",
            "work_item_type": "milestone",
        },
    )
    # orchestrated defaults to False — the interactive/operator case.
    execute_tool(db_session, "loregarden_create_ticket", args)


def test_execute_tool_orchestrated_flag_does_not_affect_undenied_tools(db_session):
    """The orchestrated check must be narrow — it must not block a tool that isn't
    in ORCHESTRATED_DENIED_MCP_TOOLS, even when orchestrated=True."""
    result = execute_tool(
        db_session,
        "loregarden_list_tickets",
        {"workspace_slug": "loregarden"},
        orchestrated=True,
    )
    assert result
