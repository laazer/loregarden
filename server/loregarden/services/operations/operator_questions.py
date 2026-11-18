"""Operator questions: an agent asks (`ask`), and anyone reads them back (`list`).

Answering is not here: it is the approval inbox's resolve, a person's act that
no agent is offered (`ui_action_catalog` keeps resolving `human_only`).
"""

from __future__ import annotations

import json
from typing import Any

from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    QuestionConfidence,
    QuestionReversibility,
    Ticket,
)
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.operator_questions import (
    AskRequest,
    AskResult,
    ask_operator,
    holds_gate,
    open_workspace_questions,
    question_meta,
    question_text,
    settled_answer,
    ticket_questions,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.organization_gate_service import workspace_for_slug
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True)


class AskOperatorCall(BaseModel):
    model_config = _LENIENT

    ticket_id: str
    """Ticket UUID or external id."""
    question: str
    """The question, in words the operator can answer without opening the code."""
    options: list[str] = Field(min_length=2, max_length=6)
    """2-6 distinct choices; the default must be one of them."""
    default: str
    """The option you are proceeding with if nobody answers."""
    confidence: QuestionConfidence
    """How sure you are of the default. 'low' stops the stage."""
    reversibility: QuestionReversibility
    """What undoing the default costs: easy (a stage re-runs), costly (redo a stage's work or
    rewrite tests), irreversible (stops the stage)."""
    affects: str
    """The stage whose work would change if the operator picks another option, at or before
    yours. An override sends the ticket back to it."""
    blocking: bool = False
    """True to stop the stage until answered. Low confidence, an irreversible default, or an
    answer that changes criteria, scope or tests stops it regardless."""
    context: str = ""
    """What you saw that raised the question: the file, the criterion, the conflict."""
    stage_key: str = ""
    """The asking stage, when the call is not made from a stage run. Defaults to the run's
    stage, then the ticket's current stage."""


class ListOperatorQuestionsCall(BaseModel):
    model_config = _LENIENT

    ticket_id: str = ""
    """Ticket UUID or external id. Omit it, and name `workspace_slug`, for every open
    question across that workspace's tickets."""
    workspace_slug: str = ""
    """The workspace to list open questions across, when no ticket is named."""
    include_settled: bool = False
    """Include answered and ignored questions, not only open ones. One ticket only."""


def _ask(session: Session, call: AskOperatorCall, caller: Caller) -> dict[str, Any]:
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=call.ticket_id)
    result: AskResult = ask_operator(
        session,
        ticket,
        AskRequest(
            question=call.question,
            options=call.options,
            default=call.default,
            confidence=call.confidence,
            reversibility=call.reversibility,
            affects=call.affects.strip(),
            blocking=call.blocking,
            context=call.context,
            stage_key=call.stage_key.strip(),
            run_id=caller.run_id,
        ),
    )
    return {
        "ticket": ticket.external_id,
        "question_id": result.approval.id,
        "blocking": result.blocking,
        "reasons": [reason.value for reason in result.reasons],
        "already_asked": result.existing,
        "filed": not result.displaced,
        "next_step": result.next_step,
    }


def question_row(approval: Approval) -> dict[str, Any]:
    """One operator question as every surface reads it."""
    meta = question_meta(approval)
    outcome, answer = settled_answer(approval)
    options = (
        json.loads(approval.tool_input_json or "{}").get("questions", [{}])[0].get("options", [])
    )
    return {
        "id": approval.id,
        "stage_key": approval.stage_key,
        "question": meta.question or question_text(approval),
        "options": [str(option.get("label", "")) for option in options],
        "default": meta.default,
        "confidence": meta.confidence.value,
        "reversibility": meta.reversibility.value,
        "affects": meta.affects,
        "context": meta.context,
        "status": approval.status.value,
        "outcome": outcome,
        "answer": answer,
        "holds_gate": holds_gate(approval),
        "created_at": approval.created_at,
        "resolved_at": approval.resolved_at,
    }


def _list_workspace(session: Session, slug: str) -> dict[str, Any]:
    workspace = workspace_for_slug(session, slug)
    rows = open_workspace_questions(session, workspace.id)
    tickets = {
        ticket.id: ticket
        for ticket in session.exec(
            select(Ticket).where(col(Ticket.id).in_({row.ticket_id for row in rows}))
        ).all()
    }
    return {
        "workspace": workspace.slug,
        "open": len(rows),
        "holding_gate": sum(1 for row in rows if holds_gate(row)),
        "questions": [
            {
                **question_row(row),
                "ticket": tickets[row.ticket_id].external_id,
                "ticket_title": tickets[row.ticket_id].title,
            }
            for row in rows
            if row.ticket_id in tickets
        ],
    }


def _list(session: Session, call: ListOperatorQuestionsCall, _caller: Caller) -> dict[str, Any]:
    if not call.ticket_id.strip():
        if not call.workspace_slug.strip():
            raise ValueError("name a ticket_id, or a workspace_slug to list across its tickets")
        return _list_workspace(session, call.workspace_slug.strip())
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=call.ticket_id)
    rows = ticket_questions(session, ticket)
    open_rows = [row for row in rows if row.status is ApprovalStatus.PENDING]
    shown = rows if call.include_settled else open_rows
    return {
        "ticket": ticket.external_id,
        "open": len(open_rows),
        "holding_gate": sum(1 for row in open_rows if holds_gate(row)),
        "total": len(rows),
        "questions": [question_row(row) for row in shown],
    }


OPERATOR_QUESTIONS = (
    Operation(
        id=OperationId.OPERATOR_QUESTION_ASK,
        effect=Effect.WRITE,
        args=AskOperatorCall,
        handler=_ask,
        unavailable_on={
            Transport.REST: "An operator answers these questions in the inbox; agents ask them.",
        },
    ),
    Operation(
        id=OperationId.OPERATOR_QUESTION_LIST,
        effect=Effect.READ,
        args=ListOperatorQuestionsCall,
        handler=_list,
    ),
)
