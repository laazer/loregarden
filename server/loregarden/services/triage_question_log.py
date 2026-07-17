"""Record answered agent questions in the triage conversation.

Baxter's questions do not travel the chat rail. An AskUserQuestion tool call is intercepted
by the permission bridge and becomes an approval the operator answers on a card; the answer
goes back to the CLI process as a tool result. The model therefore keeps its continuity, but
the human-visible transcript never sees the exchange — the chat jumps from the operator's
message to a reply that answers a question nobody can find.

Mirror the resolved exchange into triage_messages so the transcript reads whole. This is a
record, not a second place to answer: the approval card remains the only way to respond.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import Approval, ApprovalKind, TriageMessage
from sqlmodel import Session

TRIAGE_STAGE_KEY = "triage"


def _question_texts(tool_input: dict[str, Any]) -> list[tuple[str, str]]:
    """(header, question) for each well-formed question in the payload."""
    pairs: list[tuple[str, str]] = []
    for item in tool_input.get("questions") or []:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        pairs.append((str(item.get("header") or "").strip(), question))
    return pairs


def format_question_message(tool_input: dict[str, Any]) -> str:
    pairs = _question_texts(tool_input)
    if not pairs:
        return ""
    if len(pairs) == 1:
        return pairs[0][1]
    return "\n".join(f"- {question}" for _, question in pairs)


def format_answer_message(
    tool_input: dict[str, Any],
    answers: dict[str, str | list[str]] | None,
    *,
    response: str = "",
) -> str:
    """The operator's reply, quoting each question so the pairing survives out of context."""
    if response.strip():
        return response.strip()

    answers = answers or {}
    lines: list[str] = []
    for _, question in _question_texts(tool_input):
        answer = answers.get(question)
        if isinstance(answer, list):
            text = ", ".join(str(part).strip() for part in answer if str(part).strip())
        else:
            text = str(answer or "").strip()
        if not text:
            continue
        lines.append(f"- {question}\n  {text}")

    if not lines:
        return ""
    if len(lines) == 1:
        # A single answer needs no question echoed back — the message above it is the question.
        return lines[0].split("\n  ", 1)[1]
    return "\n".join(lines)


def record_triage_question_exchange(
    session: Session,
    approval: Approval,
    tool_input: dict[str, Any],
    *,
    answers: dict[str, str | list[str]] | None,
    response: str = "",
) -> list[TriageMessage]:
    """Append the question and its answer to the ticket's triage chat.

    Only questions raised from the triage conversation itself are recorded; questions a stage
    run asks belong to that run, not to a conversation the operator never had. Returns the
    messages added (empty when this approval is not a recordable triage exchange).
    """
    if approval.kind != ApprovalKind.CLI_QUESTION:
        return []
    if approval.stage_key != TRIAGE_STAGE_KEY:
        return []

    question_text = format_question_message(tool_input)
    answer_text = format_answer_message(tool_input, answers, response=response)
    if not question_text or not answer_text:
        return []

    messages = [
        TriageMessage(
            ticket_id=approval.ticket_id,
            role="assistant",
            content=question_text,
            run_id=approval.run_id,
        ),
        TriageMessage(
            ticket_id=approval.ticket_id,
            role="user",
            content=answer_text,
            run_id=approval.run_id,
        ),
    ]
    for message in messages:
        session.add(message)
    return messages
