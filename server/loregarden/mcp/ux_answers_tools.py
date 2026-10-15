"""`loregarden_record_ux_answers`: a ticket's answers for its PR's user-facing surfaces section.

The answers are validated by the rules the "PR UX section" check applies, so a
bad one is refused when it is recorded rather than found in CI on the tree's PR
(`services.ux_answers`, `services.tree_ux_section`).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError
from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import string_prop, tool_schema
from loregarden.models.domain import ArtifactKind
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ux_answers import UxAnswers

_ANSWER_FIELDS = ("question", "action", "real_data", "no_change_reason")

UX_ANSWERS_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.RECORD_UX_ANSWERS,
        "description": (
            "Record what a ticket's user-facing change is for, for the 'User-facing surfaces' "
            "section of the PR that ships it: the question the surface answers for the "
            "operator, the action it leads to, and what it showed on real data (with a number), "
            "or no_change_reason when nobody can see the change. Required for any ticket that "
            "changes client/src/pages or client/src/components: the tree's PR is assembled "
            "from these and fails its UX check without them. Validated on write; the latest "
            "record wins."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop("Ticket UUID or external id."),
                "question": string_prop(
                    "One sentence from the operator's side: what this surface answers for them."
                ),
                "action": string_prop("The link, button or filter they use next."),
                "real_data": string_prop(
                    "What it showed on real data (task sandbox), with at least one measured number."
                ),
                "no_change_reason": string_prop(
                    "Instead of the three: why nobody can see this change (20+ characters)."
                ),
            },
            required=["ticket_id"],
        ),
    }
]


def _record_ux_answers(session: Session, arguments: dict[str, Any]) -> str:
    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(ticket_id=arguments["ticket_id"])
    try:
        answers = UxAnswers.model_validate(
            {name: arguments.get(name) or "" for name in _ANSWER_FIELDS}
        )
    except ValidationError as exc:
        reasons = "; ".join(str(error["msg"]) for error in exc.errors())
        raise ValueError(f"UX answers for {ticket.external_id} not recorded: {reasons}") from exc
    artifact = svc.attach_artifact(
        ticket,
        kind=ArtifactKind.UX_ANSWERS,
        title="UX answers",
        content=answers.model_dump(),
    )
    return json.dumps(
        {"ok": True, "artifact_id": artifact.id, "ticket": ticket.external_id}, indent=2
    )


#: Registered in `tool_registry.EXTENDED_TOOLS`, not `execute_tool`'s chain.
HANDLERS = {McpTool.RECORD_UX_ANSWERS.value: _record_ux_answers}
