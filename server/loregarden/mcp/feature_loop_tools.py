"""Baxter's half of a chat feature loop: proposing acceptance criteria.

Auto-approved, like `propose_initiative_schedule`: it files *proposals* only.
The operator accepting one in the chat's Feature card is the write that
reaches the ticket (`services.chat_feature_loop`).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ConfigDict, Field, ValidationError
from sqlmodel import Session, SQLModel

from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import BaxterChatSession
from loregarden.services.chat_feature_loop import FeatureLoopError, propose_criteria

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.PROPOSE_FEATURE_CRITERIA,
        "description": (
            "Propose acceptance criteria for the feature a Baxter chat is building. Each is "
            "a draft until the operator accepts it in the chat's Feature card; accepted ones "
            "are added to the ticket and run through its workflow in the next round. Use the "
            "chat_session_id named in the chat's Feature loop section. Duplicates of criteria "
            "already in the loop or on the ticket are skipped. Returns what was proposed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_session_id": {"type": "string", "description": "The chat's session id."},
                "criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "One small, testable criterion per entry.",
                },
            },
            "required": ["chat_session_id", "criteria"],
            "additionalProperties": False,
        },
    },
]


class _ProposeRequest(SQLModel):
    model_config = ConfigDict(extra="forbid")

    chat_session_id: str
    criteria: list[str] = Field(min_length=1)


def propose_feature_criteria(session: Session, arguments: dict[str, Any]) -> str:
    try:
        request = _ProposeRequest.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(
            f"invalid arguments for {McpTool.PROPOSE_FEATURE_CRITERIA.value}: {exc}"
        ) from exc
    chat_session = session.get(BaxterChatSession, request.chat_session_id.strip())
    if chat_session is None:
        raise ValueError(f"Chat session not found: {request.chat_session_id}")
    try:
        rows = propose_criteria(session, chat_session, request.criteria)
    except FeatureLoopError as exc:
        raise ValueError(str(exc)) from exc
    return json.dumps(
        {
            "proposed": [{"id": row.id, "text": row.text} for row in rows],
            "skipped_as_duplicates": len(request.criteria) - len(rows),
            "next": "The operator accepts or drops these in the chat's Feature card.",
        },
        indent=2,
    )


HANDLERS = {McpTool.PROPOSE_FEATURE_CRITERIA.value: propose_feature_criteria}
