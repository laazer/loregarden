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
    BEGIN_EXTERNAL_STAGE = "loregarden_begin_external_stage"
    FINISH_EXTERNAL_STAGE = "loregarden_finish_external_stage"
    SKIP_STAGE = "loregarden_skip_stage"
    BLOCK_TICKET = "loregarden_block_ticket"
    ATTACH_EVIDENCE = "loregarden_attach_evidence"
    ATTACH_ARTIFACT = "loregarden_attach_artifact"
    REQUEST_APPROVAL = "loregarden_request_approval"
    COMPLETE_ORCHESTRATION = "loregarden_complete_orchestration"
    UPDATE_TICKET = "loregarden_update_ticket"
    LINK_DEPENDENCY = "loregarden_link_dependency"
    UNLINK_DEPENDENCY = "loregarden_unlink_dependency"
    LINK_RELATION = "loregarden_link_relation"
    UNLINK_RELATION = "loregarden_unlink_relation"
    CREATE_TICKET = "loregarden_create_ticket"
    MOVE_TICKET_WORKSPACE = "loregarden_move_ticket_workspace"
    SET_TICKET_WORKFLOW = "loregarden_set_ticket_workflow"
    REQUEUE_TICKET = "loregarden_requeue_ticket"
    SUPERSEDE_TICKET = "loregarden_supersede_ticket"
    MEMORY_STATUS = "loregarden_memory_status"
    APPEND_LEARNING = "loregarden_append_learning"
    UPSERT_MEMORY = "loregarden_upsert_memory"
    UPSERT_BLOG_POST = "loregarden_upsert_blog_post"
    APPEND_CHECKPOINT = "loregarden_append_checkpoint"
    WRITE_HANDOFF = "loregarden_write_handoff"
    SEARCH_PRIOR_WORK = "loregarden_search_prior_work"
    SEARCH_MEMORY = "loregarden_search_memory"
    CREATE_MEMORY_RELATION = "loregarden_create_memory_relation"
    CHECK_ORGANIZATION = "loregarden_check_organization"
    DOCTOR = "loregarden_doctor"
    FETCH_REFERENCE = "loregarden_fetch_reference"
    SEARCH_REFERENCE = "loregarden_search_reference"

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
    # Stage work reads library documentation. Offering the cache means the raw
    # HTML is paid for once per URL rather than once per run, and a stage that
    # is not offered it reaches for WebFetch instead.
    McpTool.FETCH_REFERENCE,
    # Offered beside it, because the pair is a two-step flow: search finds the
    # exact page, fetch reads it. A stage given only the fetcher guesses URLs.
    McpTool.SEARCH_REFERENCE,
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
    McpTool.LINK_RELATION,
    McpTool.UNLINK_RELATION,
    McpTool.SEARCH_PRIOR_WORK,
    McpTool.SUPERSEDE_TICKET,
)

# --- Permission-bridge policy (auto-approve vs inbox vs hard deny) ------------

READ_ONLY_MCP_TOOLS: frozenset[McpTool] = frozenset(
    {
        McpTool.GET_TICKET,
        McpTool.GET_TICKET_BY_EXTERNAL,
        McpTool.LIST_TICKETS,
        McpTool.MEMORY_STATUS,
        McpTool.SEARCH_MEMORY,
        McpTool.DOCTOR,
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

#: Tools that reach the network. Auto-approved, but kept out of the two sets
#: above rather than folded into them: `CONTROL_PLANE_WRITE_MCP_TOOLS` promises
#: its members "cannot touch the repo, the filesystem outside the vault, or
#: workflow state", and a tool that makes an outbound request is a different
#: claim. The reason to auto-approve is the same one WebFetch is auto-approved
#: for in `agents/executors/tool_auto_approve.py`: the persisted allowlist keys
#: on the exact `tool_input`, so a per-URL prompt is a prompt on every distinct
#: URL — an approval nobody can meaningfully grant in advance, spending the
#: run's timeout budget to no benefit. Egress itself is bounded elsewhere: the
#: SSRF guard rejects non-global addresses on every hop, and the body is capped.
NETWORK_EGRESS_MCP_TOOLS: frozenset[McpTool] = frozenset(
    {McpTool.FETCH_REFERENCE, McpTool.SEARCH_REFERENCE}
)

AUTO_APPROVED_MCP_TOOLS: frozenset[McpTool] = (
    READ_ONLY_MCP_TOOLS | CONTROL_PLANE_WRITE_MCP_TOOLS | NETWORK_EGRESS_MCP_TOOLS
)

#: Tools whose safety depends on *which* action was asked for, not just the tool
#: name. `check_organization` reads a workspace for one action and rewrites that
#: workspace's git hooks for another; auto-approving the name would auto-approve
#: the write. `argument_gated_auto_approval` decides per call.
ARGUMENT_GATED_MCP_TOOLS: frozenset[McpTool] = frozenset({McpTool.CHECK_ORGANIZATION})

#: The operator moves triage can make on a work item: where it lives, how it
#: runs, and whether it should exist at all. Offered to the ticket rail and the
#: studio; never to a pipeline stage, which has no business rehoming the ticket
#: it was dispatched for.
TRIAGE_OPS_MCP_TOOLS: tuple[McpTool, ...] = (
    McpTool.MOVE_TICKET_WORKSPACE,
    McpTool.SET_TICKET_WORKFLOW,
    McpTool.REQUEUE_TICKET,
    McpTool.SUPERSEDE_TICKET,
)

#: Interim allowlist (a9-create-ticket-mcp-tool): orchestrated pipeline agents
#: may not spawn tickets mid-run, and none of them may rehome, re-route or
#: retire the ticket they are running — a stage clearing its own retry budget
#: would defeat the circuit breaker that stopped it. Interactive chat is exempt.
ORCHESTRATED_DENIED_MCP_TOOLS: frozenset[McpTool] = frozenset(
    {
        McpTool.CREATE_TICKET,
        # An agent this control plane dispatched is *inside* a stage; checking
        # another one out to an outside harness from there would fork the
        # pipeline it is running.
        McpTool.BEGIN_EXTERNAL_STAGE,
        McpTool.FINISH_EXTERNAL_STAGE,
        *TRIAGE_OPS_MCP_TOOLS,
    }
)

#: Arguments an orchestrated pipeline agent may not set, keyed by tool.
#:
#: `start_stage` itself stays available — a stage agent starting the next stage
#: is ordinary pipeline work. Its `force` argument is not: it clears the stage
#: retry budget's refusal, which is the circuit breaker that exists to stop the
#: very agent making the call from redispatching its own stage forever. Denying
#: the whole tool would break the pipeline; denying the argument closes the loop.
#:
#: Enforced in `mcp.tools.execute_tool`, not only in the permission bridge: the
#: bridge blanket-approves every non-denied tool on an `auto_approve` run and
#: writes no `approvals` row while doing it, and a direct `/mcp` POST never
#: reaches the bridge at all.
ORCHESTRATED_DENIED_MCP_ARGUMENTS: dict[McpTool, frozenset[str]] = {
    McpTool.START_STAGE: frozenset({"force"}),
}


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
        *TRIAGE_OPS_MCP_TOOLS,
    }
)
