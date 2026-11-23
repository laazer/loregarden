"""A park card answers for its stage only while that stage is parked.

`stage_parking.park_stage` raises a STAGE_PARK approval and moves the stage to
AWAITING. Once the stage leaves AWAITING without the card being answered (a
requeue, a fresh dispatch, a ticket moved on by hand), the card describes a
stage that no longer exists in that shape, and every answer on it acts on the
stage as it is now: Reject and Ignore set it BLOCKED (mid-run, if it is running),
and Approve arms a waiver and re-pends it.

That is how 2ce552c3 on lmkr-execution-leaves-11 sat in the inbox with no safe
button: parked for a missing report contract, requeued once the contract was
fixed, its planner ran and passed, and the card stayed pending.

So a park whose stage is not AWAITING is stale. Answering one is refused, Ignore
closes one without touching the stage, a dispatch of its stage closes it, and
the `STALE_PARKS_ARE_CLOSED` invariant closes any that remain.
"""

from __future__ import annotations

import json

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    EventType,
    StageStatus,
    Ticket,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, select

#: `Approval.resolved_by` on a park this module closed, so the inbox history
#: says no person decided it.
SUPERSEDED_BY = "superseded"


def stage_status(session: Session, ticket: Ticket, stage_key: str) -> StageStatus | None:
    """``stage_key``'s status on ``ticket``, or None when it has no such stage."""
    instance = workflow_instance_for(session, ticket.id)
    if instance is None:
        return None
    _, stages = resolve_ticket_stages(session, ticket)
    return parse_stage_map(instance, stages).get(stage_key)


def is_stale_park(session: Session, approval: Approval) -> bool:
    """True for a pending park whose stage is no longer AWAITING.

    A park with no ticket or no stage key has no stage to be stale against, and
    is left to its own resolution.
    """
    if (
        approval.kind is not ApprovalKind.STAGE_PARK
        or approval.status is not ApprovalStatus.PENDING
        or not approval.ticket_id
        or not approval.stage_key
    ):
        return False
    ticket = session.get(Ticket, approval.ticket_id)
    if ticket is None:
        return False
    return stage_status(session, ticket, approval.stage_key) is not StageStatus.AWAITING


def refuse_answering_a_stale_park(session: Session, approval: Approval) -> None:
    """Refuse, before anything is recorded, an approve or reject on a stale park.

    Either answer would move a stage the card no longer describes. Ignore is the
    way to close it, and changes nothing else (`approval_ignore.ignore_effect`).
    """
    if not is_stale_park(session, approval):
        return
    ticket = session.get(Ticket, approval.ticket_id)
    status = stage_status(session, ticket, approval.stage_key) if ticket else None
    now = status.value if status else "gone"
    raise ValueError(
        f"This card parked stage '{approval.stage_key}', which is {now} now, so answering it "
        "would change a stage it no longer describes. Ignore it to close it."
    )


def close_stale_parks(session: Session, ticket: Ticket, stage_key: str, *, why: str) -> int:
    """Close ``ticket``'s pending parks on ``stage_key`` that are stale. Commits.

    Called once the stage has left AWAITING (a dispatch sets it RUNNING first),
    so a park raised afterwards by that same dispatch's preflight is untouched.
    """
    pending = session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.stage_key == stage_key,
            Approval.kind == ApprovalKind.STAGE_PARK,
            Approval.status == ApprovalStatus.PENDING,
        )
    ).all()
    stale = [approval for approval in pending if is_stale_park(session, approval)]
    for approval in stale:
        close_park(session, approval, why)
    if stale:
        session.commit()
        for approval in stale:
            announce_closed(session, approval)
    return len(stale)


def close_park(session: Session, approval: Approval, why: str) -> None:
    approval.status = ApprovalStatus.IGNORED
    approval.resolved_at = utcnow()
    approval.resolved_by = SUPERSEDED_BY
    approval.response_json = json.dumps({"ignored_reason": why})
    session.add(approval)


def announce_closed(session: Session, approval: Approval) -> None:
    event_bus.publish(
        session,
        EventType.APPROVAL_RESOLVED,
        workspace_id=approval.workspace_id,
        ticket_id=approval.ticket_id,
        payload={"approval_id": approval.id, "approved": False, "ignored": True},
    )
