"""Classify a block by who can unblock it, and act on the kinds a person need not see.

The control plane wrote most block messages itself — a lease expiry, a reaped
parent, a missing stage report — so it can name those `harness` without
asking anyone. An agent's own `blocked` report says its kind in
`blocked_kind`; when it does not, the message decides between `human_action`
(the same phrase test the inbox already uses) and `work`. A `decision` is not
left as a dead ticket: it becomes an approval carrying the agent's options,
and resolving it records the choice and requeues the ticket itself (749).

Imports nothing from the orchestration modules, so those can import it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Artifact,
    ArtifactKind,
    BlockKind,
    EventType,
    OrchestratorDecision,
    Ticket,
    TicketState,
)
from loregarden.services.interruption_messages import (
    INTERRUPTED_RUN_MESSAGE,
    ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
    STRANDED_STAGE_MESSAGE,
    SUPERSEDED_RUN_MESSAGE,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.requeue import requeue_stage
from loregarden.services.ticket_stage_control import StageControl
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Phrases a stage report uses when it is asking for a person rather than
#: reporting a fault. Deliberately a small, literal list: a false negative
#: reads as `work`, which is where an unexplained block belongs anyway.
HUMAN_WORK_MARKERS = (
    "a human",
    "an operator",
    "human/operator",
    "manually",
    "by hand",
    "needs a person",
    "requires a person",
)

#: Openings of the messages the control plane writes for its own failures.
#: Prefixes rather than the constants themselves because several carry a
#: run code or a stage name after the fixed part.
_HARNESS_SIGNATURES = (
    INTERRUPTED_RUN_MESSAGE,
    STRANDED_STAGE_MESSAGE,
    ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
    SUPERSEDED_RUN_MESSAGE,
    "Orchestration lease expired",
    "Agent run lease expired",
    "Agent run exited successfully but emitted no parseable",
    "Environment preflight failed",
    "Baxter was interrupted by a server restart",
    "died of an infrastructure failure",
    "Workspace Trust Required",
    "usage limit",
    "could not examine",
)

#: What a decision approval is called in the inbox; the same shape a CLI
#: question uses, so the inbox renders the options without a new card.
DECISION_QUESTION_HEADER = "Decision"


def looks_like_human_work(message: str) -> bool:
    lowered = (message or "").lower()
    return any(marker in lowered for marker in HUMAN_WORK_MARKERS)


def classify_block_message(message: str) -> BlockKind:
    """The kind a block message alone supports — used when no agent declared one."""
    text = message or ""
    lowered = text.lower()
    if any(sig.lower() in lowered for sig in _HARNESS_SIGNATURES):
        return BlockKind.HARNESS
    if looks_like_human_work(text):
        return BlockKind.HUMAN_ACTION
    return BlockKind.WORK


def _decision_question(stage_key: str, message: str, options: list[str]) -> dict:
    return {
        "questions": [
            {
                "question": message,
                "header": DECISION_QUESTION_HEADER,
                "options": [{"label": option} for option in options],
                "stage_key": stage_key,
            }
        ]
    }


def _raise_decision(
    session: Session, ticket: Ticket, *, stage_key: str, message: str, options: list[str]
) -> Approval:
    existing = session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.stage_key == stage_key,
            Approval.kind == ApprovalKind.BLOCK_DECISION,
            Approval.status == ApprovalStatus.PENDING,
        )
    ).first()
    if existing is not None:
        return existing
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.BLOCK_DECISION,
        title=f"Decision needed — {ticket.title}",
        level="high",
        stage_key=stage_key,
        impact=message,
        tool_input_json=json.dumps(_decision_question(stage_key, message, options)),
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
        payload={"approval_id": approval.id, "stage_key": stage_key},
    )
    return approval


def _how_classified(declared: BlockKind | None, kind_as_written: str) -> str:
    if declared:
        return ""
    if kind_as_written:
        return (
            f" (the agent named an unknown kind {kind_as_written!r}; classified from the message)"
        )
    return " (the agent named no kind; classified from the message)"


def record_block(
    session: Session,
    ticket: Ticket,
    *,
    stage_key: str,
    message: str,
    declared: BlockKind | None = None,
    options: list[str] | None = None,
    kind_as_written: str = "",
) -> BlockKind:
    """Stamp the kind on the ticket, say so in the history, and act on it.

    `declared` is the agent's own word from its stage report; without one the
    message decides. A `decision` raises the approval that asks the question.
    """
    kind = declared or classify_block_message(message)
    ticket.block_kind = kind
    session.add(ticket)
    reason = (
        f"Block on '{stage_key}' classified as {kind.value}"
        + _how_classified(declared, kind_as_written)
        + "."
    )
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.CLASSIFIED_BLOCK,
        stage_key=stage_key,
        reason=reason,
        evidence={"block_kind": kind.value, "options": list(options or [])},
    )
    session.commit()
    if kind is BlockKind.DECISION:
        _raise_decision(
            session, ticket, stage_key=stage_key, message=message, options=list(options or [])
        )
    return kind


#: What `record_blocking_issue` leaves inline when the real message went to an
#: error artifact — classifying this text would call every long block `work`.
_ERRORS_TAB_POINTER = "hit a blocking issue — see the Errors tab"


def block_message_for(session: Session, ticket: Ticket) -> str:
    """The block's own words: the inline text, or the artifact it points at."""
    inline = ticket.blocking_issues or ""
    if _ERRORS_TAB_POINTER not in inline:
        return inline
    artifact = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.ERROR)
        .order_by(Artifact.created_at.desc())
    ).first()
    if artifact is None:
        return inline
    try:
        message = json.loads(artifact.content_json or "{}").get("message")
    except json.JSONDecodeError:
        logger.warning("error artifact %s on %s is unreadable", artifact.id, ticket.external_id)
        return inline
    return str(message or inline)


def sweep_unclassified_blocks(session: Session) -> int:
    """Classify every blocked ticket the writers did not — run by the reconciler.

    Most block writers predate the kind and are not touched (there are ~30);
    this is what makes "every block has a kind" true without editing each one.
    """
    stale = session.exec(
        select(Ticket).where(Ticket.blocking_issues != "", Ticket.block_kind.is_(None))
    ).all()
    for ticket in stale:
        record_block(
            session,
            ticket,
            stage_key=ticket.workflow_stage_key or "",
            message=block_message_for(session, ticket),
        )
    return len(stale)


def chosen_option(approval: Approval, answers: dict[str, str | list[str]] | None) -> str:
    """The option a person picked on a decision approval, as text."""
    question = json.loads(approval.tool_input_json or "{}").get("questions", [{}])[0]
    raw = (answers or {}).get(str(question.get("question") or ""), "")
    if isinstance(raw, list):  # py-org: allow-isinstance — answers are a foreign shape
        raw = ", ".join(str(part) for part in raw if str(part).strip())
    return str(raw or "").strip()


def record_decision_outcome(
    session: Session, ticket: Ticket, approval: Approval, *, choice: str
) -> None:
    """The choice, on the ticket where the next stage will read it."""
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.CONTEXT,
            title=f"Decision — {approval.stage_key}",
            content_json=json.dumps(
                {
                    "title": f"Decision — {approval.stage_key}",
                    "rows": [
                        {"k": "Question", "v": approval.impact},
                        {"k": "Choice", "v": choice},
                        {
                            "k": "Decided at",
                            "v": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        },
                    ],
                }
            ),
        )
    )
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.REQUEUED_AFTER_DECISION,
        stage_key=approval.stage_key,
        reason=f"A person chose '{choice}' for the block on '{approval.stage_key}'; requeued there.",
        evidence={"approval_id": approval.id, "choice": choice},
    )
    session.commit()


def resolve_block_decision(
    session: Session,
    orch: StageControl,
    ticket: Ticket,
    approval: Approval,
    *,
    approved: bool,
    response_text: str,
) -> bool:
    """Act on a person's answer. True when the stage was requeued and the run
    should resume; False when the block stays (rejected, with their words on it).

    The whole point is that nobody has to requeue by hand afterwards.
    """
    if not approved:
        ticket.blocking_issues = response_text.strip() or approval.impact
        session.add(ticket)
        session.commit()
        return False
    answers = json.loads(approval.response_json or "{}").get("updated_input", {}).get("answers")
    choice = chosen_option(approval, answers) or response_text.strip()
    record_decision_outcome(session, ticket, approval, choice=choice)
    requeue_stage(
        session,
        orch,
        ticket,
        stage_key=approval.stage_key,
        reason=f"Decision answered: {choice}",
        actor="workflow",
        state=TicketState.IN_PROGRESS,
    )
    return True
