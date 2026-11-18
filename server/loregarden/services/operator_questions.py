"""A workflow agent asks the operator instead of assuming, and keeps working.

The checkpoint log was where a stage's "would have asked" went, and nobody read
it: 2,062 files in a vault no inbox, ticket panel or gate looks at. Here the
question is a row in the approvals inbox carrying the default the agent is
proceeding with, and its answer has an effect:

- **Confirmed**: the row closes and nothing moves.
- **Overridden**: the answer goes on the rework ledger for the stage whose work
  depends on it (`affects`), and that stage runs again with the answer bound in
  its prompt. The reroute counts toward `MAX_REWORK_REROUTES` like any other.

Whether a question stops the stage is the agent's call with a floor under it
(`block_reasons`): low confidence, an irreversible default, or an answer that
would change criteria, scope or tests block whatever the agent said. A
blocking question is not a new mechanism: it is the decision block that
already exists (`block_classification`), whose answer requeues the stage.

Nothing holds the run until the gate, which refuses to pass while a question
below high confidence is unanswered (`gate_hold_detail`); the ticket blocks
as `BlockKind.ANSWERS`, and answering the last one requeues the gate.

The asking run is recorded in the payload, not `Approval.run_id`: run
cancellation rejects every pending approval bound to the run, and a question
must outlive the run that asked it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Artifact,
    ArtifactKind,
    BlockKind,
    EventType,
    QuestionBlockReason,
    QuestionConfidence,
    QuestionOutcome,
    QuestionReversibility,
    StageType,
    Ticket,
    TicketState,
    WorkflowStageDef,
)
from loregarden.services.block_classification import chosen_option, raise_decision
from loregarden.services.requeue import requeue_stage
from loregarden.services.rework_feedback import (
    MAX_REWORK_REROUTES,
    record_rework_feedback,
    rework_reroute_count,
)
from loregarden.services.run_concurrency import find_active_stage_run
from loregarden.services.ticket_stage_control import StageControl
from loregarden.services.workflow_service import resolve_ticket_stages
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: The agents whose stages settle what a ticket must do: its criteria, its
#: scope, and the tests that pin them. An answer that would send work back to
#: one of these changes the requirement, not just the implementation.
REQUIREMENT_AGENT_IDS = frozenset({"spec", "test_designer", "test_breaker"})

#: The header an operator question carries, so the inbox can tell it apart.
QUESTION_HEADER = "Assumption"
#: The rework ledger's `from_stage` for an operator's override.
OVERRIDE_FROM = "operator"
_MIN_OPTIONS = 2
_MAX_OPTIONS = 6


class OperatorQuestionMeta(BaseModel):
    """What the agent said alongside its question, stored in `tool_input_json`."""

    model_config = ConfigDict(extra="ignore")

    default: str
    confidence: QuestionConfidence
    reversibility: QuestionReversibility
    affects: str
    asked_at_stage: str
    asked_by_run_id: str = ""
    context: str = ""


class _StoredQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str = ""


class _StoredPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    questions: list[_StoredQuestion] = []
    operator_question: OperatorQuestionMeta


@dataclass(frozen=True)
class AskRequest:
    """One question, as the agent asked it. Validated by `ask_operator`."""

    question: str
    options: list[str]
    default: str
    confidence: QuestionConfidence
    reversibility: QuestionReversibility
    affects: str
    blocking: bool = False
    context: str = ""
    #: The stage asking; the run's stage, else the ticket's cursor.
    stage_key: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class AskResult:
    approval: Approval
    blocking: bool
    reasons: tuple[QuestionBlockReason, ...]
    #: An open question already asked in the same words on this stage was returned.
    existing: bool

    @property
    def next_step(self) -> str:
        """What the asking agent does now, in a sentence it can act on."""
        if not self.blocking:
            meta = question_meta(self.approval)
            return (
                f"Carry on with '{meta.default}'. The operator can confirm it or override it; "
                f"an override sends '{meta.affects}' back with their answer, and the gate "
                "waits for an answer if your confidence was below high."
            )
        named = ", ".join(reason.value for reason in self.reasons)
        return (
            f"This question blocks ({named}). Stop work on this stage now and report it "
            "`blocked` with blocked_kind `decision`: the question is already in the "
            f"operator's inbox (approval {self.approval.id}), and the stage runs again "
            "with their answer."
        )


def block_reasons(
    request: AskRequest,
    *,
    affects_upstream: bool,
    affects_requirements: bool,
) -> tuple[QuestionBlockReason, ...]:
    """Why this question must stop the stage, empty when it may carry on.

    The agent's own `blocking` is one reason among four; the other three are
    the floor it cannot talk its way under.
    """
    reasons: list[QuestionBlockReason] = []
    if request.blocking:
        reasons.append(QuestionBlockReason.AGENT_ASKED)
    if request.confidence is QuestionConfidence.LOW:
        reasons.append(QuestionBlockReason.LOW_CONFIDENCE)
    if request.reversibility is QuestionReversibility.IRREVERSIBLE:
        reasons.append(QuestionBlockReason.IRREVERSIBLE)
    if affects_upstream and affects_requirements:
        reasons.append(QuestionBlockReason.CHANGES_REQUIREMENTS)
    return tuple(reasons)


def _stage_agent_ids(stage: WorkflowStageDef) -> set[str]:
    ids = {stage.agent_id}
    ids.update(spec.agent_id for spec in stage.parallel_agents)
    ids.update(route.agent_id for route in stage.classify_routes)
    ids.discard("")
    return ids


def _asking_stage(session: Session, ticket: Ticket, request: AskRequest) -> str:
    if request.run_id:
        run = session.get(AgentRun, request.run_id)
        if run is not None and run.ticket_id == ticket.id and run.stage_key:
            return run.stage_key
    return (request.stage_key or ticket.workflow_stage_key or "").strip()


def _validated_options(request: AskRequest) -> list[str]:
    options = [option.strip() for option in request.options if option.strip()]
    if len(set(options)) != len(options):
        raise ValueError("options repeat; give each choice once")
    if not _MIN_OPTIONS <= len(options) <= _MAX_OPTIONS:
        raise ValueError(
            f"options needs {_MIN_OPTIONS} to {_MAX_OPTIONS} choices, got {len(options)}"
        )
    if request.default.strip() not in options:
        raise ValueError(f"default {request.default!r} is not one of the options")
    return options


def _stage_positions(
    stages: list[WorkflowStageDef], *, asking: str, affects: str
) -> tuple[int, WorkflowStageDef]:
    keys = [stage.key for stage in stages]
    if asking not in keys:
        raise ValueError(f"the asking stage {asking!r} is not in this ticket's workflow")
    if affects not in keys:
        raise ValueError(
            f"affects {affects!r} is not a stage of this ticket's workflow: {', '.join(keys)}"
        )
    if keys.index(affects) > keys.index(asking):
        raise ValueError(
            f"affects {affects!r} comes after the asking stage {asking!r}; name the stage "
            "whose work would have to change, at or before this one"
        )
    return keys.index(asking) - keys.index(affects), stages[keys.index(affects)]


def _open_duplicate(
    session: Session, ticket: Ticket, stage_key: str, question: str
) -> Approval | None:
    for approval in session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.stage_key == stage_key,
            Approval.kind == ApprovalKind.OPERATOR_QUESTION,
            Approval.status == ApprovalStatus.PENDING,
        )
    ).all():
        if question_text(approval) == question:
            return approval
    return None


def _payload(request: AskRequest, options: list[str], meta: OperatorQuestionMeta) -> str:
    return json.dumps(
        {
            "questions": [
                {
                    "question": request.question.strip(),
                    "header": QUESTION_HEADER,
                    "options": [
                        {
                            "label": option,
                            "description": "The agent's default" if option == meta.default else "",
                        }
                        for option in options
                    ],
                    "multiSelect": False,
                }
            ],
            "operator_question": meta.model_dump(mode="json"),
        }
    )


def ask_operator(session: Session, ticket: Ticket, request: AskRequest) -> AskResult:
    """File the question, or raise the decision block when it must stop the stage.

    Idempotent: asking the same words again on the same stage while the first
    is open returns the first, so a retried call files nothing twice.
    """
    question = request.question.strip()
    if not question:
        raise ValueError("question is empty")
    options = _validated_options(request)
    asking = _asking_stage(session, ticket, request)
    _, stages = resolve_ticket_stages(session, ticket)
    distance, affects_stage = _stage_positions(stages, asking=asking, affects=request.affects)
    reasons = block_reasons(
        request,
        affects_upstream=distance > 0,
        affects_requirements=bool(_stage_agent_ids(affects_stage) & REQUIREMENT_AGENT_IDS),
    )
    meta = OperatorQuestionMeta(
        default=request.default.strip(),
        confidence=request.confidence,
        reversibility=request.reversibility,
        affects=affects_stage.key,
        asked_at_stage=asking,
        asked_by_run_id=request.run_id,
        context=request.context.strip(),
    )
    if reasons:
        message = (
            f"{question}\n\nThe agent's default: '{meta.default}' "
            f"(confidence {meta.confidence.value}; its work affects '{meta.affects}')."
        )
        if meta.context:
            message = f"{message}\n\n{meta.context}"
        decision = raise_decision(
            session, ticket, stage_key=asking, message=message, options=options
        )
        return AskResult(approval=decision, blocking=True, reasons=reasons, existing=False)

    duplicate = _open_duplicate(session, ticket, asking, question)
    if duplicate is not None:
        return AskResult(approval=duplicate, blocking=False, reasons=(), existing=True)
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.OPERATOR_QUESTION,
        title=f"Assumed — {ticket.title}",
        level="medium" if meta.confidence is QuestionConfidence.HIGH else "high",
        stage_key=asking,
        impact=f"Proceeding with '{meta.default}'. {question}",
        tool_input_json=_payload(request, options, meta),
        status=ApprovalStatus.PENDING,
    )
    session.add(approval)
    session.commit()
    session.refresh(approval)
    event_bus.publish(
        session,
        EventType.APPROVAL_REQUESTED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        payload={"approval_id": approval.id, "stage_key": asking},
    )
    return AskResult(approval=approval, blocking=False, reasons=(), existing=False)


def question_meta(approval: Approval) -> OperatorQuestionMeta:
    """The agent's default, confidence and target stage for an operator question."""
    return _StoredPayload.model_validate_json(approval.tool_input_json or "{}").operator_question


def question_text(approval: Approval) -> str:
    payload = _StoredPayload.model_validate_json(approval.tool_input_json or "{}")
    return payload.questions[0].question if payload.questions else ""


def ticket_questions(session: Session, ticket: Ticket) -> list[Approval]:
    """Every operator question on the ticket, open ones first, newest first within."""
    rows = session.exec(
        select(Approval)
        .where(
            Approval.ticket_id == ticket.id,
            Approval.kind == ApprovalKind.OPERATOR_QUESTION,
        )
        .order_by(col(Approval.created_at).desc())
    ).all()
    return sorted(rows, key=lambda row: row.status is not ApprovalStatus.PENDING)


def holds_gate(approval: Approval) -> bool:
    """Whether this open row keeps the gate from passing."""
    if approval.status is not ApprovalStatus.PENDING:
        return False
    if approval.kind is ApprovalKind.BLOCK_DECISION:
        return True
    return (
        approval.kind is ApprovalKind.OPERATOR_QUESTION
        and question_meta(approval).confidence is not QuestionConfidence.HIGH
    )


def gate_holding_questions(session: Session, ticket: Ticket) -> list[Approval]:
    """Open questions the gate waits for: a decision block, or an assumption below high."""
    rows = session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.status == ApprovalStatus.PENDING,
            col(Approval.kind).in_([ApprovalKind.OPERATOR_QUESTION, ApprovalKind.BLOCK_DECISION]),
        )
    ).all()
    return [row for row in rows if holds_gate(row)]


def gate_hold_detail(session: Session, ticket: Ticket) -> str:
    """Why the gate may not pass yet, naming each question; "" when it may.

    Ids first: a long block message is cut inline, and the ids are what the
    operator opens.
    """
    holding = gate_holding_questions(session, ticket)
    if not holding:
        return ""
    ids = ", ".join(row.id for row in holding)
    lines = [f"- {row.id} ({row.stage_key}): {question_text(row) or row.impact}" for row in holding]
    return (
        f"Waiting on {len(holding)} answer(s) in the inbox: {ids}. The gate passes once the "
        "operator answers them.\n" + "\n".join(lines)
    )


def is_gate_stage(stage: WorkflowStageDef) -> bool:
    return stage.stage_type == StageType.GATE


@dataclass(frozen=True)
class _Answer:
    outcome: QuestionOutcome
    text: str


def _answer(
    approval: Approval, answers: dict[str, str | list[str]] | None, response_text: str
) -> _Answer:
    meta = question_meta(approval)
    chosen = chosen_option(approval, answers) or response_text.strip()
    if not chosen or chosen == meta.default:
        return _Answer(QuestionOutcome.CONFIRMED, meta.default)
    return _Answer(QuestionOutcome.OVERRIDDEN, chosen)


def refuse_unappliable_answer(
    session: Session,
    approval: Approval,
    *,
    approved: bool,
    answers: dict[str, str | list[str]] | None,
    response_text: str,
) -> None:
    """Raise before the row closes when its answer could not take effect.

    A reject has no meaning (confirm or override it; ignore closes it without
    an answer). An override cannot reroute while a stage is running on the
    ticket, nor past the rework cap; either way the question stays open and
    the refusal says what to do instead.
    """
    if approval.kind is not ApprovalKind.OPERATOR_QUESTION:
        return
    if not approved:
        raise ValueError(
            "An assumption is confirmed or overridden, not rejected. Pick the answer you "
            "want; to close it without one, ignore it with a reason."
        )
    if _answer(approval, answers, response_text).outcome is QuestionOutcome.CONFIRMED:
        return
    ticket = session.get(Ticket, approval.ticket_id) if approval.ticket_id else None
    if ticket is None:
        raise ValueError("This question's ticket no longer exists; nothing to send back.")
    running = find_active_stage_run(session, ticket.id)
    if running is not None:
        raise ValueError(
            f"Stage '{running.stage_key}' is running on {ticket.external_id}, so the override "
            "cannot send work back yet. Answer again once it finishes; the gate waits for "
            "this question."
        )
    affects = question_meta(approval).affects
    if rework_reroute_count(session, ticket, affects) >= MAX_REWORK_REROUTES:
        raise ValueError(
            f"'{affects}' has already been sent back {MAX_REWORK_REROUTES} times on "
            f"{ticket.external_id}, the rework cap. Confirm the default, or requeue the "
            "ticket naming the unmet criterion that justifies another round."
        )


def _record_answer(session: Session, ticket: Ticket, approval: Approval, answer: _Answer) -> None:
    meta = question_meta(approval)
    stored = json.loads(approval.response_json or "{}")
    stored.update({"outcome": answer.outcome.value, "answer": answer.text})
    approval.response_json = json.dumps(stored)
    session.add(approval)
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.CONTEXT,
            title=f"Operator answer — {approval.stage_key}",
            content_json=json.dumps(
                {
                    "title": f"Operator answer — {approval.stage_key}",
                    "rows": [
                        {"k": "Question", "v": question_text(approval)},
                        {"k": "Agent's default", "v": meta.default},
                        {"k": "Answer", "v": answer.text},
                        {"k": "Outcome", "v": answer.outcome.value},
                        {
                            "k": "Decided at",
                            "v": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        },
                    ],
                }
            ),
        )
    )


def _override_context(approval: Approval, answer: _Answer) -> str:
    meta = question_meta(approval)
    return (
        f"The operator overrode an assumption made at '{meta.asked_at_stage}'. "
        f"Question: {question_text(approval)}\n"
        f"The agent assumed: {meta.default}\n"
        f"BINDING DECISION — use this instead: {answer.text}\n"
        "Redo this stage's work where it relied on the assumption."
    )


def _release_answers_hold(session: Session, orch: StageControl, ticket: Ticket) -> bool:
    """Requeue the gate a ticket was held at once nothing holds it. True if it was."""
    if ticket.block_kind is not BlockKind.ANSWERS or gate_holding_questions(session, ticket):
        return False
    requeue_stage(
        session,
        orch,
        ticket,
        stage_key=ticket.workflow_stage_key,
        reason="Every question the gate was waiting for has an answer.",
        actor="human",
        state=TicketState.IN_PROGRESS,
    )
    return True


def resolve_operator_question(
    session: Session,
    orch: StageControl,
    ticket: Ticket,
    approval: Approval,
    *,
    approved: bool,
) -> bool:
    """Act on a closed operator question. True when the orchestration should resume.

    `approved=False` arrives only from ignore (a reject is refused up front),
    and closes the question without an answer. Answers were already validated
    by `refuse_unappliable_answer`, so an override here always applies.
    """
    if not approved:
        return _release_answers_hold(session, orch, ticket)
    stored = json.loads(approval.response_json or "{}").get("updated_input", {})
    answer = _answer(approval, stored.get("answers"), str(stored.get("response") or ""))
    _record_answer(session, ticket, approval, answer)
    session.commit()
    if answer.outcome is QuestionOutcome.CONFIRMED:
        return _release_answers_hold(session, orch, ticket)
    affects = question_meta(approval).affects
    record_rework_feedback(
        session,
        ticket,
        target_stage=affects,
        from_stage=OVERRIDE_FROM,
        context=_override_context(approval, answer),
    )
    if ticket.block_kind is BlockKind.ANSWERS and ticket.workflow_stage_key != affects:
        # The gate is BLOCKED in the stage map; left so, the reroute would run
        # back up to a gate that can never start again.
        requeue_stage(
            session,
            orch,
            ticket,
            stage_key=ticket.workflow_stage_key,
            reason="An override sends the work back past the gate's hold for answers.",
            actor="human",
            state=TicketState.IN_PROGRESS,
        )
    requeue_stage(
        session,
        orch,
        ticket,
        stage_key=affects,
        reason=f"The operator overrode an assumption: {answer.text}",
        actor="human",
        state=TicketState.IN_PROGRESS,
    )
    logger.info(
        "operator override on %s sent %s back (approval %s)",
        ticket.external_id,
        affects,
        approval.id,
    )
    return True
