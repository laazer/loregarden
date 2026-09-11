"""The inbox item a stopped rework loop files: a decision, not a dead end.

`MAX_REWORK_REROUTES` and the convergence check both stop a loop that is not
getting anywhere. Stopping was all they did. The ticket went BLOCKED, the rounds
that led there stayed in artifacts nobody was pointed at, and — on the parallel
path — the block message's own words ("Paused for a human") matched the handover
heuristic in `orchestration_callbacks._looks_like_human_work`, so the pause was
filed as a HUMAN_ACTION carrying an empty `PreparedAction` and `assess_handover`
findings telling an agent to prepare something no agent had been asked for.
Neither of that card's buttons touched the ticket, because `ApprovalService`
only applies a resolution for gate kinds. The standalone path filed nothing at
all.

So a pause files a `REWORK_PAUSE`, which resolves through the same routing the
workflow gate uses: approve accepts the stage and carries on, reject sends the
work back — to the reject target by default, or to a stage the operator names.
The accumulated feedback is rendered onto the card, so the decision is made
against the findings rather than against a pointer to them.

Its own module because both callers need it and neither can import the other:
`orchestration_callbacks` reaches it after blocking under orchestration, and
`run_completion` after blocking a standalone run, while
`orchestration_callbacks` → `orchestration` → `run_completion` already closes a
cycle in the other direction. Nothing here needs `OrchestrationService`.
"""

from __future__ import annotations

import json
import logging

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    EventType,
    Ticket,
)
from loregarden.services.rework_feedback import render_rework_feedback
from pydantic import BaseModel, ValidationError
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: How much accumulated rework feedback a pause card renders inline. Long enough
#: for the several rounds it takes to reach the cap, short enough that one round
#: of a stack trace cannot bury the decision the card is asking for. A round of
#: `sqlite3.OperationalError: database is locked` ran to twelve thousand
#: characters on the ticket this was written for.
PAUSE_FEEDBACK_CHAR_LIMIT = 4000


class ReworkPausePayload(BaseModel):
    """What a REWORK_PAUSE approval carries beyond its prose.

    `target_stage` is the stage whose reroute ledger hit the cap — the one whose
    budget a resolution gives back. Carried on the row rather than re-derived at
    resolution time: by then the ticket may have been rerouted, and re-deriving
    would reset the budget of whichever stage it happens to point at instead of
    the one the operator was actually asked about.
    """

    target_stage: str = ""


def rework_pause_target(approval_payload: str) -> str:
    """The target stage a pause was raised about, or "" if the row carries none.

    An empty answer means "reset no budget", which is what a row written before
    this payload existed should get. An *unreadable* payload is a different
    thing and is logged: the resolution still applies (refusing it would strand
    the pause it exists to clear), but a budget silently left in place is how a
    resolved pause comes straight back on the next round, so it must not pass
    unremarked.
    """
    if not approval_payload:
        return ""
    try:
        return ReworkPausePayload.model_validate_json(approval_payload).target_stage
    except ValidationError:
        logger.warning(
            "rework pause carries an unreadable payload (%.120s); its loop budget is left "
            "in place, so the next round may pause again",
            approval_payload,
            exc_info=True,
        )
        return ""


def _pause_impact(session: Session, ticket: Ticket, *, target_stage: str, message: str) -> str:
    """The card's body: why it stopped, then the rounds that led there.

    Rendered as Markdown by `ApprovalCard`, which is why the feedback goes here
    rather than into `checklist_json` — that list is labelled "notes only, not
    required to approve", and the rounds are the evidence the decision rests on.
    """
    feedback = render_rework_feedback(session, ticket, target_stage)
    if not feedback:
        return message
    if len(feedback) > PAUSE_FEEDBACK_CHAR_LIMIT:
        feedback = (
            f"{feedback[:PAUSE_FEEDBACK_CHAR_LIMIT]}\n\n"
            "*(truncated — the full rounds are on the ticket's artifacts)*"
        )
    return f"{message}\n\n## Accumulated rework feedback — `{target_stage}`\n\n{feedback}"


def file_rework_pause(
    session: Session,
    ticket: Ticket,
    *,
    stage_key: str,
    target_stage: str,
    message: str,
) -> Approval:
    """Put a stopped rework loop in the inbox as a decision someone can make.

    The caller has already blocked the ticket; this is the half that gives the
    block an action. `REWORK_PAUSE` and not `WORKFLOW_GATE` because
    `subtree_auto_run.auto_resolve_awaiting_gate` looks for a pending gate on the
    stage and auto-approves it: an unattended run would sign off its own pause
    and walk straight through the cap that exists to stop it looping. The kind is
    the guard — `ApprovalService.auto_resolve` refuses anything that is not a
    workflow gate, so the failure direction is a loud refusal rather than a
    silent approval.
    """
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.REWORK_PAUSE,
        title=f"Rework paused — {ticket.title}",
        level="high",
        stage_key=stage_key,
        impact=_pause_impact(session, ticket, target_stage=target_stage, message=message),
        checklist_json=json.dumps(
            [
                f"Approve to accept '{stage_key}' as passed and carry on.",
                f"Reject to send the work back — to '{target_stage}' by default, or to the "
                "stage you name under Routing.",
                "Either resolution gives the loop its budget back, so the next round is not "
                "paused again for this same reason.",
            ]
        ),
        tool_input_json=ReworkPausePayload(target_stage=target_stage).model_dump_json(),
        status=ApprovalStatus.PENDING,
    )
    session.add(approval)
    session.commit()
    event_bus.publish(
        session,
        EventType.APPROVAL_REQUESTED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        payload={"approval_id": approval.id, "stage_key": stage_key},
    )
    return approval
