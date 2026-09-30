"""Record every Loregarden MCP call at the point it is dispatched.

`tool_telemetry.record_tool_call` records a *decision*, and its observation
point is the permission bridge. That vantage point went dark on 2026-08-14: a
run with permission bypass has no bridge, cursor runs `--trust --force` in
print mode, codex and the external harnesses never had one — and by August
every run was one of those. Six hundred and seventy-four rows, then nothing,
while the runs' own streams showed dozens of `loregarden_*` calls each
(lg-workflow-integrity-759).

Dispatch is the one point every adapter passes through: the HTTP `/mcp`
endpoint and the stdio server both hand `tools/call` to `protocol.handle_request`.
Recording there sees the call and its outcome, which the bridge never could —
so `decision_ms` here is real execution time, and `decision` says whether the
tool ran. It cannot see who is calling: the MCP request carries no run. The
attribution comes from the arguments — the ticket or orchestration run the
tool names — and the in-flight agent run under it. A call naming neither is
recorded unattributed rather than dropped: the count is the point.

Never raises. Telemetry that can fail a tool call is worse than none.
"""

from __future__ import annotations

import logging
from typing import Any

from loregarden.models.domain import OrchestrationRun, Ticket
from loregarden.services.run_concurrency import find_active_run
from loregarden.services.ticket_discovery import looks_like_ticket_uuid
from loregarden.services.ticket_ids import resolve as resolve_external_id
from loregarden.services.tool_policy import LOREGARDEN_SERVER
from loregarden.services.tool_telemetry import record_tool_call
from sqlmodel import Session

logger = logging.getLogger(__name__)


def _ticket_named_by(session: Session, arguments: dict[str, Any]) -> Ticket | None:
    for key in ("ticket_id", "external_id"):
        value = arguments.get(key)
        if not value:
            continue
        if looks_like_ticket_uuid(str(value)):
            found = session.get(Ticket, str(value))
            if found is not None:
                return found
        found = resolve_external_id(session, str(value))
        if found is not None:
            return found
    run_id = arguments.get("run_id")
    if run_id:
        orch = session.get(OrchestrationRun, str(run_id))
        if orch is not None:
            return session.get(Ticket, orch.ticket_id)
    return None


def record_dispatch(
    session: Session,
    *,
    name: str,
    arguments: dict[str, Any],
    decision: str,
    decision_ms: int,
) -> None:
    """One row for one `tools/call`, attributed as far as the arguments allow."""
    try:
        # Raw JSON-RPC params: a caller that sent something other than an
        # object lands here as an AttributeError and is counted unattributed.
        ticket = _ticket_named_by(session, arguments)
        run = find_active_run(session, ticket.id) if ticket is not None else None
    except Exception:  # noqa: BLE001 - attribution is best effort; the row still lands
        logger.warning("Could not attribute tool call %s", name, exc_info=True)
        ticket, run = None, None
    record_tool_call(
        session,
        run_id=run.id if run is not None else "",
        ticket_id=ticket.id if ticket is not None else "",
        agent_id=run.agent_id if run is not None else "",
        tool_name=f"mcp__{LOREGARDEN_SERVER}__{name}",
        decision=decision,
        decision_ms=decision_ms,
    )
