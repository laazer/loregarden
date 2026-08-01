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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from loregarden.models.domain import McpToolCall
from loregarden.services.tool_policy import split_mcp_tool
from sqlmodel import Session, func, select

logger = logging.getLogger(__name__)

#: Window the per-server rate is measured over. An hour rather than a minute:
#: agent traffic is bursty, and a one-minute sample reads as a server that
#: stopped working every time a stage is thinking.
RATE_WINDOW_MINUTES = 60


@dataclass
class ServerActivity:
    """What one server's traffic looked like, as the bridge saw it."""

    calls: int
    calls_in_window: int
    calls_per_min: float
    window_minutes: int
    #: Agents that actually called this server, not agents that could.
    agent_ids: list[str] = field(default_factory=list)
    #: Empty when this server has never been called.
    last_call_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


#: How a request was resolved.
DECISION_TRUSTED_SERVER = "auto_server"  # registered server with tool_policy=auto
DECISION_ALLOWLIST = "auto_allowlist"  # loregarden's curated read/bookkeeping set
DECISION_READ_ONLY_CLI = "auto_cli"  # WebFetch / WebSearch
DECISION_RUN_AUTO = "auto_run"  # the run's own auto_approve flag
DECISION_SCOPE_ALLOW = "auto_scope"  # a persisted per-ticket/stage allowance
DECISION_APPROVED = "approved"  # a human said yes
DECISION_REJECTED = "rejected"  # a human said no
DECISION_RATE_LIMITED = "rate_limited"  # refused by the server's own ceiling

DECISIONS = (
    DECISION_TRUSTED_SERVER,
    DECISION_ALLOWLIST,
    DECISION_READ_ONLY_CLI,
    DECISION_RUN_AUTO,
    DECISION_SCOPE_ALLOW,
    DECISION_APPROVED,
    DECISION_REJECTED,
    DECISION_RATE_LIMITED,
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


def server_activity(
    session: Session, *, window_minutes: int = RATE_WINDOW_MINUTES
) -> dict[str, ServerActivity]:
    """Per-server traffic, for the gateway's rails and metrics.

    The rate is calls the bridge *decided* on, divided by the window — not
    requests a proxy counted, because there is no proxy. A run with permissions
    bypassed produces no rows and so appears here as silence, which is why the
    UI names the window rather than showing a bare "req/m".
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=max(1, window_minutes))
    minutes = float(max(1, window_minutes))

    totals = counts_by_server(session)
    windowed = session.exec(
        select(McpToolCall.server_name, func.count())  # type: ignore[arg-type]
        .where(McpToolCall.created_at >= since)
        .group_by(McpToolCall.server_name)
    ).all()
    recent_counts = {str(server): int(count) for server, count in windowed if server}

    latest = session.exec(
        select(McpToolCall.server_name, func.max(McpToolCall.created_at)).group_by(  # type: ignore[arg-type]
            McpToolCall.server_name
        )
    ).all()
    last_seen = {str(server): last for server, last in latest if server}

    agent_rows = session.exec(
        select(McpToolCall.server_name, McpToolCall.agent_id).distinct()  # type: ignore[arg-type]
    ).all()
    agents: dict[str, set[str]] = {}
    for server, agent_id in agent_rows:
        if server and agent_id:
            agents.setdefault(str(server), set()).add(str(agent_id))

    return {
        server: ServerActivity(
            calls=calls,
            calls_in_window=recent_counts.get(server, 0),
            calls_per_min=round(recent_counts.get(server, 0) / minutes, 2),
            window_minutes=int(minutes),
            agent_ids=sorted(agents.get(server, set())),
            last_call_at=_isoformat(last_seen.get(server)),
        )
        for server, calls in totals.items()
    }


def _isoformat(value: datetime | str | None) -> str:
    if value is None:
        return ""
    return value.isoformat() if isinstance(value, datetime) else str(value)
