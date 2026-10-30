"""`loregarden_record_fix_coverage`: how a bug ticket's fix covers the whole bad state.

The answers are validated by the shape rules the "PR fix coverage" check
applies (`services.fix_coverage`), and the learning is written to the memory
graph in the same call, before the record: a record exists only when its
learning does.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import string_prop, tool_schema
from loregarden.models.domain import ArtifactKind
from loregarden.services.fix_coverage import parse_fix_coverage
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_workspace_binding import workspace_for_ticket

_ANSWER_FIELDS = ("existing", "reentry", "prevention", "learning", "not_a_state_bug")

FIX_COVERAGE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.RECORD_FIX_COVERAGE,
        "description": (
            "Record how a bug ticket's fix covers the whole bad state, for the 'Fix coverage' "
            "section of the PR that ships it: existing (how rows already in the state are found "
            "and repaired, or the query showing none), reentry (the invariant in "
            "services/invariant_registry.py, in backticks, that catches the state coming back, "
            "or 'no detector: <reason>'), prevention (the test proving the state cannot be "
            "entered, as path::test_name) and learning (why the state was reachable, as a rule "
            "the next change can use). Or not_a_state_bug when the fix leaves no state behind. "
            "Required before a bug ticket's spec stage passes. Writes the learning to memory, "
            "then the record; the latest record wins."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("Ticket UUID or external id."),
                "existing": string_prop(
                    "How rows already in the bad state are found and repaired, or the query "
                    "and count showing there are none."
                ),
                "reentry": string_prop(
                    "The invariant name, in backticks, that catches the state coming back; or "
                    "'no detector: <reason>'."
                ),
                "prevention": string_prop(
                    "The test proving the state cannot be entered: path/to/test_file.py::test_name."
                ),
                "learning": string_prop("Why the state was reachable, as a reusable rule."),
                "not_a_state_bug": string_prop(
                    "Instead of the four: why this fix leaves no state behind (20+ characters)."
                ),
            },
            required=["ticket_id"],
        ),
    }
]


def _record_fix_coverage(session: Session, arguments: dict[str, Any]) -> str:
    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    try:
        coverage = parse_fix_coverage({name: arguments.get(name) or "" for name in _ANSWER_FIELDS})
    except ValueError as exc:
        raise ValueError(f"Fix coverage for {ticket.external_id} not recorded: {exc}") from exc
    learning: dict[str, Any] = {}
    if coverage.learning:
        workspace = workspace_for_ticket(session, ticket)
        if workspace is None:
            raise ValueError(f"{ticket.external_id} has no workspace to record its learning in")
        learning = AgentMemoryService.from_settings().append_learning(
            ticket_id=ticket.external_id,
            workspace_slug=workspace.slug,
            content=coverage.learning,
            title=coverage.learning,
            tags=["fix-coverage", *(f"invariant:{name}" for name in coverage.invariants)],
        )
    artifact = svc.attach_artifact(
        ticket,
        kind=ArtifactKind.FIX_COVERAGE,
        title="Fix coverage",
        content=coverage.model_dump(),
    )
    return json.dumps(
        {
            "ok": True,
            "artifact_id": artifact.id,
            "ticket": ticket.external_id,
            "learning": learning,
        },
        indent=2,
        default=str,
    )


#: Registered in `tool_registry.EXTENDED_TOOLS`, not `execute_tool`'s chain.
HANDLERS = {McpTool.RECORD_FIX_COVERAGE.value: _record_fix_coverage}
