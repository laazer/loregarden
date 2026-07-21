"""A ceiling on how fast a registered server may be called.

U1c lets an operator trust a whole server, after which its tools run unattended.
That is the right trade for a server you trust, and it removes the only thing
that was pacing an agent: a human clicking. A loop that calls a trusted tool
every few hundred milliseconds will do so until the run ends.

The limit is enforced where the trust decision is made, not in a proxy — the
permission bridge already sees every call, so nothing new has to sit in the
request path.

Counting reads the telemetry U1d already records, which is why it counts only
calls that actually proceeded: a refused call never reached the server and must
not consume its budget, or a burst of refusals would keep the server locked out.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from loregarden.models.domain import McpServer, McpToolCall
from loregarden.services.tool_telemetry import DECISION_RATE_LIMITED, DECISION_REJECTED
from sqlmodel import Session, func, select

logger = logging.getLogger(__name__)

WINDOW = timedelta(minutes=1)

#: Decisions that did not reach the server, so they do not consume its budget.
_DID_NOT_RUN = (DECISION_REJECTED, DECISION_RATE_LIMITED)


def calls_in_window(session: Session, server_name: str) -> int:
    """Calls to this server in the last minute that actually ran."""
    since = datetime.now(timezone.utc) - WINDOW
    statement = (
        select(func.count())
        .select_from(McpToolCall)
        .where(
            McpToolCall.server_name == server_name,
            McpToolCall.created_at >= since,
            McpToolCall.decision.notin_(_DID_NOT_RUN),
        )
    )
    return int(session.exec(statement).one() or 0)


def rate_limit_denial(session: Session, server_name: str) -> str:
    """Why this call must be refused, or "" when it may proceed.

    Returns a sentence rather than a bool: it is sent back to the agent as the
    tool's failure, so it has to explain itself well enough that the agent can
    decide to back off rather than retry immediately.

    Never raises. A limit that cannot be read is not a reason to refuse work —
    the failure mode of this feature must be "no limit", not "no tools".
    """
    try:
        server = session.exec(select(McpServer).where(McpServer.name == server_name)).first()
        if not server or server.rate_limit_per_min <= 0:
            return ""

        used = calls_in_window(session, server_name)
        if used < server.rate_limit_per_min:
            return ""
        return (
            f"Rate limit reached for '{server_name}': {used} calls in the last minute, "
            f"limit {server.rate_limit_per_min}. Wait before calling it again."
        )
    except Exception:  # noqa: BLE001 - a broken limit must not block the run
        logger.warning("Could not evaluate the rate limit for %s", server_name, exc_info=True)
        return ""
