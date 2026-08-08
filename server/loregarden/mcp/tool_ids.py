"""Canonical Loregarden MCP tool ids.

One ``StrEnum`` is the source of truth for tool names. Policy sets (stage
defaults, auto-approve, orchestrated deny, …) are groupings of those members —
not parallel string lists that drift out of sync.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class McpTool(StrEnum):
    """Every Loregarden MCP tool the control plane exposes."""

    GET_TICKET = "loregarden_get_ticket"
    LIST_TICKETS = "loregarden_list_tickets"
    GET_TICKET_BY_EXTERNAL = "loregarden_get_ticket_by_external"
    START_ORCHESTRATION = "loregarden_start_orchestration"
    START_STAGE = "loregarden_start_stage"
    COMPLETE_STAGE = "loregarden_complete_stage"
    SKIP_STAGE = "loregarden_skip_stage"
    BLOCK_TICKET = "loregarden_block_ticket"
    ATTACH_EVIDENCE = "loregarden_attach_evidence"
    ATTACH_ARTIFACT = "loregarden_attach_artifact"
    REQUEST_APPROVAL = "loregarden_request_approval"
    COMPLETE_ORCHESTRATION = "loregarden_complete_orchestration"
    UPDATE_TICKET = "loregarden_update_ticket"
    LINK_DEPENDENCY = "loregarden_link_dependency"
    UNLINK_DEPENDENCY = "loregarden_unlink_dependency"
    CREATE_TICKET = "loregarden_create_ticket"
    MEMORY_STATUS = "loregarden_memory_status"
    APPEND_LEARNING = "loregarden_append_learning"
    UPSERT_MEMORY = "loregarden_upsert_memory"
    UPSERT_BLOG_POST = "loregarden_upsert_blog_post"
    APPEND_CHECKPOINT = "loregarden_append_checkpoint"
    WRITE_HANDOFF = "loregarden_write_handoff"
    SEARCH_PRIOR_WORK = "loregarden_search_prior_work"
    SEARCH_MEMORY = "loregarden_search_memory"
    CREATE_MEMORY_RELATION = "loregarden_create_memory_relation"

    @classmethod
    def try_parse(cls, name: str) -> McpTool | None:
        try:
            return cls(name)
        except ValueError:
            return None


def mcp_tool_values(tools: Iterable[McpTool]) -> list[str]:
    """Stable ``list[str]`` for JSON / CLI argv / DB columns."""
    return [tool.value for tool in tools]


# --- Grant defaults (what an agent is offered) --------------------------------

STAGE_DEFAULT_MCP_TOOLS: tuple[McpTool, ...] = (
    McpTool.GET_TICKET,
    McpTool.LIST_TICKETS,
    McpTool.ATTACH_ARTIFACT,
    # A stage that must produce evidence needs the tool to record it, or it is
    # blocked with no way to comply.
    McpTool.ATTACH_EVIDENCE,
    McpTool.REQUEST_APPROVAL,
)

MEMORY_DEFAULT_MCP_TOOLS: tuple[McpTool, ...] = (
    McpTool.MEMORY_STATUS,
    McpTool.SEARCH_MEMORY,
    McpTool.APPEND_LEARNING,
    McpTool.UPSERT_MEMORY,
    McpTool.UPSERT_BLOG_POST,
    McpTool.CREATE_MEMORY_RELATION,
)

TICKET_STUDIO_MCP_TOOLS: tuple[McpTool, ...] = (
    McpTool.GET_TICKET,
    McpTool.GET_TICKET_BY_EXTERNAL,
    McpTool.LIST_TICKETS,
    McpTool.CREATE_TICKET,
    McpTool.UPDATE_TICKET,
    McpTool.LINK_DEPENDENCY,
    McpTool.UNLINK_DEPENDENCY,
    McpTool.SEARCH_PRIOR_WORK,
)

# --- Permission-bridge policy (auto-approve vs inbox vs hard deny) ------------

READ_ONLY_MCP_TOOLS: frozenset[McpTool] = frozenset(
    {
        McpTool.GET_TICKET,
        McpTool.GET_TICKET_BY_EXTERNAL,
        McpTool.LIST_TICKETS,
        McpTool.MEMORY_STATUS,
        McpTool.SEARCH_MEMORY,
    }
)

# Bookkeeping writes that land only in Loregarden's own stores — the Obsidian
# vault, the memory graph, and the artifacts table. They cannot touch the repo,
# the filesystem outside the vault, or workflow state, so gating them behind a
# human click buys no safety: it just spends the run's timeout budget.
#
# Deliberately excluded — these mutate workflow state or write repo files, and
# stay gated on stage runs: complete_stage, skip_stage, block_ticket,
# update_ticket, write_handoff, request_approval, start/complete_orchestration,
# start_stage.
CONTROL_PLANE_WRITE_MCP_TOOLS: frozenset[McpTool] = frozenset(
    {
        McpTool.APPEND_CHECKPOINT,
        McpTool.APPEND_LEARNING,
        McpTool.UPSERT_MEMORY,
        McpTool.CREATE_MEMORY_RELATION,
        McpTool.UPSERT_BLOG_POST,
        McpTool.ATTACH_ARTIFACT,
        McpTool.ATTACH_EVIDENCE,
        McpTool.SEARCH_PRIOR_WORK,
    }
)

AUTO_APPROVED_MCP_TOOLS: frozenset[McpTool] = READ_ONLY_MCP_TOOLS | CONTROL_PLANE_WRITE_MCP_TOOLS

#: Interim allowlist (a9-create-ticket-mcp-tool): orchestrated pipeline agents
#: may not spawn tickets mid-run. Interactive chat is exempt.
ORCHESTRATED_DENIED_MCP_TOOLS: frozenset[McpTool] = frozenset({McpTool.CREATE_TICKET})

# Ticket-scoped chat enrichment: fill ticket_id when the open work item is known.
TICKET_SCOPED_MCP_TOOLS: frozenset[McpTool] = frozenset(
    {
        McpTool.UPDATE_TICKET,
        McpTool.BLOCK_TICKET,
        McpTool.ATTACH_ARTIFACT,
        McpTool.ATTACH_EVIDENCE,
        McpTool.APPEND_CHECKPOINT,
        McpTool.APPEND_LEARNING,
        McpTool.WRITE_HANDOFF,
        McpTool.REQUEST_APPROVAL,
        McpTool.START_ORCHESTRATION,
        McpTool.START_STAGE,
        McpTool.COMPLETE_STAGE,
        McpTool.SKIP_STAGE,
        McpTool.COMPLETE_ORCHESTRATION,
        McpTool.SEARCH_PRIOR_WORK,
    }
)
