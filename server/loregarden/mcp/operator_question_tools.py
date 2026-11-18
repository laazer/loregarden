"""The operator-question tools' names and descriptions; their schemas come from
the operation's args model (`services/operations/operator_questions.py`)."""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_ids import McpTool

OPERATOR_QUESTION_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.ASK_OPERATOR,
        "description": (
            "Ask the operator a question you would otherwise settle by assumption, and keep "
            "working on the default you name. Use it for every 'would have asked' in place of "
            "a checkpoint entry. The question goes to the inbox; a confirm closes it, an "
            "override sends the `affects` stage back with the operator's answer bound in its "
            "prompt. It stops the stage instead (blocking=true in the reply) when you ask it "
            "to, when confidence is 'low', when the default is 'irreversible', or when "
            "`affects` is an earlier stage that settles criteria, scope or tests; then report "
            "the stage blocked with blocked_kind 'decision'. The gate waits for any question "
            "below 'high' confidence to be answered."
        ),
    },
    {
        "name": McpTool.LIST_OPERATOR_QUESTIONS,
        "description": (
            "The questions agents have asked the operator on a ticket: each one's default, "
            "confidence, the stage it affects, and whether it holds the gate; with "
            "include_settled, the answers too. With no ticket_id and a workspace_slug, every "
            "open question across that workspace. Answering is the operator's: in chat, stage "
            "their answer on an `operator_question` card for them to confirm."
        ),
    },
]
