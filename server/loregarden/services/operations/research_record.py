"""A research run on the record: its question, its candidates, and a person's refutations.

`loregarden_research_record` and the research page's refute route both called
`research.refute`, each in its own words. One operation now holds all three
actions; the page offers only `refute`, and each transport keeps its reply (an
agent the claim's id and status, the page the claim card).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loregarden.models.domain.survey_enums import ClaimRecorder, ResearchRecordAction
from loregarden.models.domain.survey_research_tables import SurveyCapabilityClaim
from loregarden.services.operations.advertised import items_like
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    OperationUnavailable,
    Transport,
)
from loregarden.services.reference_survey import research
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True)

_CANDIDATES = TypeAdapter(list[research.CandidateIn])


class ResearchRecordCall(BaseModel):
    """The tool's flat arguments; each action reads its own, and its service
    model validates them (`QuestionIn`, `CandidateIn`, `RefuteIn`)."""

    model_config = _LENIENT

    action: ResearchRecordAction
    """What to record."""
    thread_id: str = ""
    """question: this thread's chat_session_id."""
    question: str = ""
    """question: the capability question, in a sentence."""
    capabilities: list[str] = []
    """question: what a candidate must do, one per item (at most 8)."""
    question_id: str | None = None
    """candidates/refute: the question it answers."""
    candidates: list[dict[str, Any]] = Field(
        default_factory=list, json_schema_extra=items_like(research.CandidateIn)
    )
    """candidates: the projects put forward."""
    url: str = ""
    """refute: the project URL."""
    capability: str = ""
    """refute: what it does not do."""
    note: str = ""
    """refute: the operator's words, or why."""


@dataclass(frozen=True)
class Recorded:
    #: What an agent is answered with.
    agent: dict[str, Any]
    #: The claim a refutation recorded, for the page's card; None otherwise.
    claim: SurveyCapabilityClaim | None = None


def _question(session: Session, call: ResearchRecordCall, _caller: Caller) -> Recorded:
    question = research.open_question(
        session,
        research.QuestionIn(
            thread_id=call.thread_id, question=call.question, capabilities=call.capabilities
        ),
    )
    return Recorded(
        agent={"question_id": question.id, "capabilities": research.capabilities(question)}
    )


def _candidates(session: Session, call: ResearchRecordCall, _caller: Caller) -> Recorded:
    result = research.record_candidates(
        session, call.question_id or "", _CANDIDATES.validate_python(call.candidates)
    )
    return Recorded(agent=result.model_dump(mode="json"))


def _refute(session: Session, call: ResearchRecordCall, caller: Caller) -> Recorded:
    # A refutation from the page is a person's and stands over agent claims;
    # an agent's is only another claim (`_person_refuted`).
    recorder = ClaimRecorder.OPERATOR if caller.transport is Transport.REST else ClaimRecorder.AGENT
    claim = research.refute(
        session,
        research.RefuteIn(
            url=call.url,
            capability=call.capability,
            note=call.note,
            question_id=call.question_id or None,
        ),
        recorded_by=recorder,
    )
    return Recorded(
        agent={"claim_id": claim.id, "url": claim.url, "status": claim.status.value}, claim=claim
    )


_ACTIONS: dict[ResearchRecordAction, Callable[[Session, ResearchRecordCall, Caller], Recorded]] = {
    ResearchRecordAction.QUESTION: _question,
    ResearchRecordAction.CANDIDATES: _candidates,
    ResearchRecordAction.REFUTE: _refute,
}

#: What the page offers. A question and its candidates are a research run's
#: own record; the page reads them, and only a person's correction is written there.
_ON_REST = frozenset({ResearchRecordAction.REFUTE})


def _record(session: Session, call: ResearchRecordCall, caller: Caller) -> Recorded:
    if caller.transport is Transport.REST and call.action not in _ON_REST:
        raise OperationUnavailable(
            OperationId.RESEARCH_RECORD,
            caller.transport,
            f"action {call.action.value!r} is a research run's own record; the page refutes",
        )
    return _ACTIONS[call.action](session, call, caller)


def _agent_reply(_session: Session, recorded: Recorded) -> dict[str, Any]:
    return recorded.agent


def _page_reply(_session: Session, recorded: Recorded) -> research.ClaimView:
    return research.ClaimView.model_validate(recorded.claim, from_attributes=True)


RESEARCH_RECORD = (
    Operation(
        id=OperationId.RESEARCH_RECORD,
        effect=Effect.WRITE,
        args=ResearchRecordCall,
        handler=_record,
        replies={
            Transport.MCP: _agent_reply,
            Transport.CLI: _agent_reply,
            Transport.REST: _page_reply,
        },
    ),
)
