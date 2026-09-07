"""The dispatch budget is a guardrail, not a boundary — and says so.

lg-workflow-integrity-584 decided this after three review rounds on 560, each of
which closed a bypass and found another. The design question underneath: is the
adversary a confused agent that wanders into the wrong tool, or one that routes
around an obstacle?

Guardrail. `orchestrated` comes from a header the agent's own `--mcp-config`
carries, so an agent with a shell can send the same request without it.
Authentication cannot close that: the agent must reach `/mcp` to do its job, so
any credential it holds is one it can reuse.

These tests pin the POSITION, not the impossible guarantee. What must not drift
is the deny-list's membership and the honesty of what it claims.
"""

from loregarden.mcp.tool_ids import ORCHESTRATED_DENIED_MCP_TOOLS, McpTool
from loregarden.models.domain import Workspace
from sqlmodel import Session, select


def test_start_orchestration_is_denied_to_an_orchestrated_agent():
    """AC5, first half. An agent inside a stage starting an orchestration forks
    the pipeline it is running — the same reason BEGIN_EXTERNAL_STAGE is denied.
    """
    assert McpTool.START_ORCHESTRATION in ORCHESTRATED_DENIED_MCP_TOOLS


def test_block_ticket_stays_available_to_an_orchestrated_agent():
    """AC5, second half, and the more interesting one.

    Blocking is the sanctioned way to stop when genuinely stuck; denying it would
    push agents to invent worse signals. The bypass it once enabled — block,
    restart orchestration, wipe the counter — was closed in 560 by narrowing
    `clear_budget` to `budget_reset_pending` alone, so the tool no longer buys
    what it used to.
    """
    assert McpTool.BLOCK_TICKET not in ORCHESTRATED_DENIED_MCP_TOOLS


def test_the_denylist_does_not_claim_to_be_a_boundary():
    """AC2. The docstring used to overstate what this guarantees, which is how
    each review round graded a way around it as Critical.

    Asserted rather than trusted: a docstring that drifts back to promising
    enforcement would restart that cycle.
    """
    import loregarden.mcp.tool_ids as tool_ids

    source = tool_ids.__doc__ or ""
    module = __import__("inspect").getsource(tool_ids)
    doc = module[: module.index("ORCHESTRATED_DENIED_MCP_TOOLS: frozenset")]

    assert "guardrail" in doc.lower(), "the deny-list must say what it is"
    assert "not a security boundary" in doc.lower() or "NOT a security boundary" in doc
    assert "header" in doc.lower(), (
        "the caller-supplied flag must be named where a reviewer meets it"
    )
    del source


def test_the_agents_own_config_carries_the_flag_it_is_judged_by():
    """The fact that makes this a guardrail, pinned so it is not rediscovered as
    a surprise in a fourth review round."""
    from loregarden.agents.mcp_context import loregarden_mcp_server_entry

    entry = loregarden_mcp_server_entry(orchestrated=True)
    headers = entry.get("headers") or entry.get("env") or {}
    assert any("Orchestrated" in key or "ORCHESTRATED" in key for key in headers), (
        "the orchestrated flag travels in the agent's own config, which is why "
        "the deny-list cannot be a boundary"
    )


def test_no_auth_credential_reaches_the_agents_mcp_config(db_session: Session):
    """Why authentication is not the fix.

    `/mcp` is not exempt from `LOREGARDEN_API_TOKEN` — only `/health` is — but
    the agent's config carries no token, so setting one breaks every agent's MCP
    tools rather than protecting them. All-or-nothing, not a gradient. If a token
    were injected here, the agent could read its own config and reuse it.
    """
    from loregarden.agents.mcp_context import loregarden_mcp_server_entry

    entry = loregarden_mcp_server_entry(orchestrated=True)
    blob = repr(entry).lower()
    assert "token" not in blob and "authorization" not in blob
    # keeps the fixture honest about which workspace this ran against
    assert db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
