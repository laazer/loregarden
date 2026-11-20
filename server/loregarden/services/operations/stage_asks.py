"""A stage run asks an earlier stage on its ticket one question (`services.stage_ask`)."""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import AgentRun
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.stage_ask import StageAskRefused, ask_earlier_stage
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True)


class AskEarlierStageCall(BaseModel):
    model_config = _LENIENT

    ticket_id: str
    """Your ticket: UUID or external id."""
    stage_key: str
    """The earlier stage to ask, as the ticket's workflow names it (e.g. plan, spec, implement)."""
    question: str
    """One question: a fact it found, or the reason behind a decision it made."""


def _ask(session: Session, call: AskEarlierStageCall, caller: Caller) -> dict[str, Any]:
    asker = session.get(AgentRun, caller.run_id) if caller.orchestrated and caller.run_id else None
    if asker is None:
        raise StageAskRefused(
            "only a stage run Loregarden supervises can ask an earlier stage; read the "
            "ticket's artifacts and runs instead"
        )
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=call.ticket_id)
    answered = ask_earlier_stage(session, asker, ticket, call.stage_key.strip(), call.question)
    reply: dict[str, Any] = {
        "ticket": ticket.external_id,
        "stage_key": answered.target.stage_key,
        "answered_by_run": answered.target.run_code,
        "ask_run": answered.ask_run.run_code,
        "answer": answered.answer,
    }
    if not answered.prompt_kept:
        reply["note"] = (
            "the earlier run's system prompt was not kept, so it answered without its "
            "stage contract"
        )
    return reply


STAGE_ASKS = (
    Operation(
        id=OperationId.STAGE_ASK,
        effect=Effect.WRITE,
        args=AskEarlierStageCall,
        handler=_ask,
        unavailable_on={
            Transport.REST: "Asked by a stage run of the ticket; an operator reads the runs instead.",
        },
    ),
)
