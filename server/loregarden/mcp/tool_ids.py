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
    RESERVE_DOCKER_CAPACITY = "loregarden_reserve_docker_capacity"
    RENEW_DOCKER_LEASE = "loregarden_renew_docker_lease"
    RELEASE_DOCKER_CAPACITY = "loregarden_release_docker_capacity"
    DOCKER_CAPACITY_STATUS = "loregarden_docker_capacity_status"
    FORCE_RELEASE_DOCKER_LEASE = "loregarden_force_release_docker_lease"

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
        McpTool.DOCKER_CAPACITY_STATUS,
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

#: Reserve, renew and release on the docker capacity ledger. Auto-approved, and
#: held beside `CONTROL_PLANE_WRITE_MCP_TOOLS` rather than folded into it for the
#: same reason `NETWORK_EGRESS_MCP_TOOLS` is: that set promises its members
#: cannot touch workflow state, and a scheduler ledger that decides whether
#: another run may start is workflow state by any honest reading.
#:
#: Auto-approved anyway, and the argument is concrete. An agent that has to wait
#: for a human click to *release* a lease will simply not release it, and
#: capacity leaks until the reaper notices — so gating the release makes the
#: ledger less accurate, not safer. A gated *reserve* is worse still: it turns a
#: two-second wait into a wait bounded by how long somebody takes to look at an
#: inbox, spent out of the run's own timeout budget. And every one of the three
#: is undone by the reaper, which is what makes auto-approval cheap here.
#:
#: `FORCE_RELEASE_DOCKER_LEASE` is deliberately NOT in this set: it takes
#: capacity away from something that may still be running, which is the one
#: action here that can hurt a peer.
CAPACITY_LEASE_MCP_TOOLS: frozenset[McpTool] = frozenset(
    {
        McpTool.RESERVE_DOCKER_CAPACITY,
        McpTool.RENEW_DOCKER_LEASE,
        McpTool.RELEASE_DOCKER_CAPACITY,
    }
)

#: Offered to an agent that needs to book docker capacity itself. Deliberately
#: NOT in `STAGE_DEFAULT_MCP_TOOLS`: a tool an agent is offered is a tool it will
#: find a reason to call, and most stages never start a container.
#:
#: A stage that declares a `docker_footprint` does not need these — the
#: orchestrator takes its lease around the whole run
#: (`services/stage_docker_capacity.py`), so the capacity is already held before
#: the agent's first turn. These are for the ad-hoc case: a human at the CLI, or
#: an agent starting a stack outside a stage that declared one. Add them to a
#: workspace's agent tool list to grant them.
DOCKER_CAPACITY_MCP_TOOLS: tuple[McpTool, ...] = (
    McpTool.RESERVE_DOCKER_CAPACITY,
    McpTool.RENEW_DOCKER_LEASE,
    McpTool.RELEASE_DOCKER_CAPACITY,
    McpTool.DOCKER_CAPACITY_STATUS,
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
    READ_ONLY_MCP_TOOLS
    | CONTROL_PLANE_WRITE_MCP_TOOLS
    | NETWORK_EGRESS_MCP_TOOLS
    | CAPACITY_LEASE_MCP_TOOLS
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

#: Tools an orchestrated pipeline agent may not call. Interactive chat is exempt.
#:
#: WHAT THIS IS: a GUARDRAIL against a confused or looping agent, decided in
#: lg-workflow-integrity-584. It is NOT a security boundary, and grading a way
#: around it as Critical mistakes what it was built to do.
#:
#: WHY IT CANNOT BE A BOUNDARY. `orchestrated` comes from the
#: `X-Loregarden-Orchestrated` header the agent's own `--mcp-config` carries
#: (`agents/mcp_context.py`), so an agent with a shell can send the same request
#: without it. Authentication does not fix that: the agent MUST be able to reach
#: `/mcp` to do its job, so any credential it holds is one it can reuse. Today
#: the config carries no token at all, which means setting `LOREGARDEN_API_TOKEN`
#: would break every agent's MCP tools rather than protect them — it is
#: all-or-nothing, not a gradient.
#:
#: The one change that WOULD make this a boundary is deriving `orchestrated`
#: from the run rather than the request. That touches every tool's dispatch and
#: is deliberately not done here; see 584 for the conditions under which it is
#: worth revisiting.
#:
#: So: this stops an agent that wanders into the wrong tool. It does not stop one
#: that decides to route around it, and it is not trying to.
ORCHESTRATED_DENIED_MCP_TOOLS: frozenset[McpTool] = frozenset(
    {
        #: Force-releasing takes docker capacity away from something that may
        #: still be running. An agent that finds the pool full has a legitimate
        #: reason to want this and no way to know whose work it would break.
        McpTool.FORCE_RELEASE_DOCKER_LEASE,
        #: Orchestrated agents may not spawn tickets mid-run
        #: (a9-create-ticket-mcp-tool).
        McpTool.CREATE_TICKET,
        # An agent this control plane dispatched is *inside* a stage; checking
        # another one out to an outside harness from there would fork the
        # pipeline it is running.
        McpTool.BEGIN_EXTERNAL_STAGE,
        McpTool.FINISH_EXTERNAL_STAGE,
        # Same reasoning as BEGIN_EXTERNAL_STAGE, and the position 584 AC5 asked
        # for: an agent inside a stage starting an orchestration forks the
        # pipeline it is running. Nothing legitimate breaks — the control plane
        # starts child orchestrations itself through `subtree_auto_run`, not
        # through this tool.
        #
        # BLOCK_TICKET is deliberately NOT here, which is the other half of that
        # position. It is the sanctioned way for an agent to stop when it is
        # genuinely stuck, and denying it would push agents to invent worse
        # signals. The bypass it used to enable — block, restart, wipe the
        # counter — was closed in 560 by narrowing `clear_budget` to
        # `budget_reset_pending` alone.
        McpTool.START_ORCHESTRATION,
        #: None of them may rehome, re-route or retire the ticket they run.
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
