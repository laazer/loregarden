"""McpTool StrEnum is the single catalog for Loregarden MCP tool ids."""

from loregarden.mcp.tool_ids import (
    AUTO_APPROVED_MCP_TOOLS,
    MEMORY_DEFAULT_MCP_TOOLS,
    ORCHESTRATED_DENIED_MCP_TOOLS,
    STAGE_DEFAULT_MCP_TOOLS,
    TICKET_STUDIO_MCP_TOOLS,
    McpTool,
)
from loregarden.mcp.tools import tool_names


def test_mcp_tool_enum_covers_every_registered_tool():
    registered = set(tool_names())
    enumerated = {tool.value for tool in McpTool}
    assert enumerated == registered


def test_policy_sets_only_reference_known_tools():
    known = set(McpTool)
    for group in (
        STAGE_DEFAULT_MCP_TOOLS,
        MEMORY_DEFAULT_MCP_TOOLS,
        TICKET_STUDIO_MCP_TOOLS,
        AUTO_APPROVED_MCP_TOOLS,
        ORCHESTRATED_DENIED_MCP_TOOLS,
    ):
        assert set(group) <= known


def test_str_enum_membership_accepts_bare_tool_names():
    assert McpTool.CREATE_TICKET in ORCHESTRATED_DENIED_MCP_TOOLS
    assert "loregarden_create_ticket" in ORCHESTRATED_DENIED_MCP_TOOLS
    assert "loregarden_create_ticket" not in AUTO_APPROVED_MCP_TOOLS
    assert "loregarden_attach_evidence" in AUTO_APPROVED_MCP_TOOLS
