"""What agents asked to do, and how each request was resolved.

Recorded at the permission bridge, which is the only place the control plane
sees a tool call at all. That vantage point decides what can honestly be
measured:

- **Counts, by server, tool and agent** — yes. Every call that reaches a
  decision is one row.
- **How it was resolved** — yes. Trusted-server, allowlist, run-wide
  auto-approve, or a human, and which way the human went.
- **How long the decision took** — yes, and for a prompted call that is how
  long the operator took.
- **Tool execution latency and success** — *no*. The CLI runs the tool itself
  and reports nothing back, so a duration or an error rate here would be
  invented. Those need the proxy (U1b).

Two blind spots worth stating rather than discovering later: runs with
permission bypass enabled have no bridge, and the cursor adapter is print-mode,
so neither produces rows.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import McpToolCall
from loregarden.services.tool_policy import split_mcp_tool
from sqlmodel import Session, func, select

logger = logging.getLogger(__name__)

#: How a request was resolved.
DECISION_TRUSTED_SERVER = "auto_server"  # registered server with tool_policy=auto
DECISION_ALLOWLIST = "auto_allowlist"  # loregarden's curated read/bookkeeping set
DECISION_READ_ONLY_CLI = "auto_cli"  # WebFetch / WebSearch
DECISION_RUN_AUTO = "auto_run"  # the run's own auto_approve flag
DECISION_SCOPE_ALLOW = "auto_scope"  # a persisted per-ticket/stage allowance
DECISION_APPROVED = "approved"  # a human said yes
DECISION_REJECTED = "rejected"  # a human said no

DECISIONS = (
    DECISION_TRUSTED_SERVER,
    DECISION_ALLOWLIST,
    DECISION_READ_ONLY_CLI,
    DECISION_RUN_AUTO,
    DECISION_SCOPE_ALLOW,
    DECISION_APPROVED,
    DECISION_REJECTED,
)


def record_tool_call(
    session: Session,
    *,
    run_id: str,
    ticket_id: str,
    agent_id: str,
    tool_name: str,
    decision: str,
    decision_ms: int = 0,
) -> None:
    """Record one decision. Never raises.

    Telemetry that can fail a run is worse than no telemetry: the agent's work
    is the point, and a full disk or a locked table must not end it.
    """
    try:
        split = split_mcp_tool(tool_name)
        session.add(
            McpToolCall(
                run_id=run_id,
                ticket_id=ticket_id,
                agent_id=agent_id,
                tool_name=tool_name,
                server_name=split[0] if split else "",
                decision=decision,
                decision_ms=max(0, int(decision_ms)),
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("Could not record tool call %s", tool_name, exc_info=True)
        session.rollback()


def recent_calls(session: Session, *, limit: int = 50) -> list[McpToolCall]:
    return list(
        session.exec(select(McpToolCall).order_by(McpToolCall.created_at.desc()).limit(limit)).all()
    )


def counts_by_server(session: Session) -> dict[str, int]:
    """Calls per MCP server. Non-MCP tools are grouped under "" by the query
    and dropped here — this is the MCP gateway's view, not every tool."""
    rows = session.exec(
        select(McpToolCall.server_name, func.count()).group_by(McpToolCall.server_name)  # type: ignore[arg-type]
    ).all()
    return {str(server): int(count) for server, count in rows if server}


def counts_by_decision(session: Session) -> dict[str, int]:
    rows = session.exec(
        select(McpToolCall.decision, func.count()).group_by(McpToolCall.decision)  # type: ignore[arg-type]
    ).all()
    return {str(decision): int(count) for decision, count in rows if decision}
