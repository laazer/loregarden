"""An approved question's answers, and an approved permission's allow rules.

Moved unchanged out of `OrchestrationService` (it was
`_record_question_or_permission_response`), which was over its size cap. The
`permission_bridge` import stays inside the function, as it was: that module
imports `orchestration`, which imports this one.
"""

from __future__ import annotations

import json

from loregarden.models.domain import Approval, ApprovalKind
from loregarden.services.permission_allowlist import (
    add_ticket_allow_rule,
    add_workspace_allow_rule,
)
from loregarden.services.triage_question_log import (
    record_home_chat_question_exchange,
    record_triage_question_exchange,
)
from sqlmodel import Session

#: Approval kinds answered with `answers` against a `tool_input_json` question —
#: an agent's AskUserQuestion, and a decision block asked the same way (749).
_QUESTION_APPROVAL_KINDS = frozenset({ApprovalKind.CLI_QUESTION, ApprovalKind.BLOCK_DECISION})


def record_approval_answer(
    session: Session,
    approval: Approval,
    *,
    answers: dict[str, str | list[str]] | None,
    response_text: str,
    always_allow: bool,
    allow_for_ticket: bool,
    allow_for_stage: bool,
) -> None:
    """Store an approved question's answers, or a permission's allow rules, on `approval`."""
    from loregarden.agents.executors.permission_bridge import (
        build_ask_user_question_input,
        parse_stored_tool_input,
        validate_question_answers,
    )

    if approval.kind in _QUESTION_APPROVAL_KINDS:
        tool_input = json.loads(approval.tool_input_json or "{}")
        validate_question_answers(tool_input, answers, response=response_text)
        updated_input = build_ask_user_question_input(
            tool_input,
            answers=answers or {},
            response=response_text,
        )
        approval.response_json = json.dumps({"updated_input": updated_input})
        # The answer reaches the agent as a tool result; mirror it into the chat so the
        # operator's transcript shows the exchange rather than jumping over it.
        record_triage_question_exchange(
            session,
            approval,
            tool_input,
            answers=answers,
            response=response_text,
        )
        record_home_chat_question_exchange(
            session,
            approval,
            tool_input,
            answers=answers,
            response=response_text,
        )
        return
    if approval.kind != ApprovalKind.CLI_PERMISSION:
        return
    tool_input = parse_stored_tool_input(approval.tool_input_json)
    approval.response_json = json.dumps({"updated_input": tool_input})
    if always_allow:
        add_workspace_allow_rule(
            session,
            approval.workspace_id,
            approval.tool_name,
            tool_input,
        )
    # Ticket/stage allow rules need a work item; Home chat approvals are
    # workspace-scoped and can only persist always_allow.
    if allow_for_ticket and approval.ticket_id:
        add_ticket_allow_rule(
            session,
            approval.ticket_id,
            approval.tool_name,
            tool_input,
        )
    if allow_for_stage and approval.ticket_id and approval.stage_key:
        add_ticket_allow_rule(
            session,
            approval.ticket_id,
            approval.tool_name,
            tool_input,
            stage_key=approval.stage_key,
        )
