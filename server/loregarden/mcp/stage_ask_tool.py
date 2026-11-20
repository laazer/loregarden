"""`loregarden_ask_earlier_stage`'s name and description; its schema comes from the
operation's args model (`services/operations/stage_asks.py`)."""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.stage_ask import MAX_ASKS_PER_RUN

STAGE_ASK_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.ASK_EARLIER_STAGE,
        "description": (
            "Ask an earlier stage on your own ticket one question, instead of re-deriving what "
            "it already knows: where it found something, or why it decided something. Its "
            "session answers from what it did, read-only, in a minute or two; the call waits. "
            f"Up to {MAX_ASKS_PER_RUN} per run. Refused for judging stages (review, gate, "
            "verify), for your own stage, and when that stage has no resumable claude session; "
            "then read its artifacts and notes."
        ),
    },
]
