"""Ignoring an approval: what it does to whatever is waiting, decided in one place.

An inbox card can outlive its reason. The ticket finished, a second card says
the same thing, it was filed by a test. Approve and Reject both assert a
decision about the work, which is wrong for a card nobody needs to decide.
Ignore closes it with a reason instead (`ApprovalStatus.IGNORED`).

Closing a card must not strand what is waiting on it. A run blocked on a
permission prompt would poll until its timeout, and a parked stage would sit
with no card left to say why. So ignoring answers a live waiter the way Reject
would, and `ignore_effect` says beforehand which waiter that is, so the inbox
can warn before the click.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from loregarden.core.event_bus import event_bus
from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    EventType,
    IgnoreEffect,
    Ticket,
)
from loregarden.services.run_concurrency import IN_FLIGHT_STATUSES
from sqlmodel import Session

#: Kinds a live agent process polls for an answer (`permission_bridge`).
_AGENT_WAITER_KINDS = frozenset({ApprovalKind.CLI_PERMISSION, ApprovalKind.CLI_QUESTION})
#: Kinds a ticket's stage is parked on until someone answers.
_STAGE_WAITER_KINDS = frozenset(
    {
        ApprovalKind.WORKFLOW_GATE,
        ApprovalKind.REWORK_PAUSE,
        ApprovalKind.STAGE_PARK,
        ApprovalKind.BLOCK_DECISION,
    }
)

logger = logging.getLogger(__name__)

#: The longest reason kept. A reason is a sentence, not a report.
MAX_IGNORE_REASON = 500


def ignore_effect(session: Session, approval: Approval) -> IgnoreEffect:
    """What ignoring ``approval`` now would do to whatever waits on it."""
    if approval.kind in _AGENT_WAITER_KINDS:
        run = session.get(AgentRun, approval.run_id) if approval.run_id else None
        if run is not None and run.status in IN_FLIGHT_STATUSES:
            return IgnoreEffect.DENIES_AGENT
        return IgnoreEffect.NONE
    if approval.kind in _STAGE_WAITER_KINDS and approval.ticket_id:
        ticket = session.get(Ticket, approval.ticket_id)
        if ticket is not None and ticket.state not in StateMachine.TERMINAL_TICKET_STATES:
            return IgnoreEffect.DECLINES_STAGE
    return IgnoreEffect.NONE


def validated_ignore_reason(reason: str) -> str:
    """The reason as stored. Raises ``ValueError`` for an empty or oversized one:
    an ignored card with no reason is a dismissal nobody can audit."""
    cleaned = reason.strip()
    if not cleaned:
        raise ValueError("Ignoring an approval needs a reason")
    if len(cleaned) > MAX_IGNORE_REASON:
        raise ValueError(f"Ignore reason is over {MAX_IGNORE_REASON} characters")
    return cleaned


def ignore_approval(
    session: Session,
    approval_id: str,
    *,
    reason: str,
    decline_stage: Callable[[Ticket, Approval, str], None],
) -> Approval:
    """Mark ``approval`` ignored with ``reason`` and answer whatever waits on it.

    A parked stage is answered through ``decline_stage``, the same ticket
    resolution Reject runs; a blocked agent's poller reads `IGNORED` as a
    denial (`denial_message`). Raises ``ValueError`` for a missing reason, a
    missing approval, or one already resolved.
    """
    cleaned = validated_ignore_reason(reason)
    approval = session.get(Approval, approval_id)
    if not approval:
        raise ValueError("Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise ValueError("Approval already resolved")
    effect = ignore_effect(session, approval)

    approval.status = ApprovalStatus.IGNORED
    approval.resolved_at = datetime.now(timezone.utc)
    approval.response_json = json.dumps({"ignored_reason": cleaned})
    session.add(approval)
    session.commit()

    if effect is IgnoreEffect.DECLINES_STAGE:
        ticket = session.get(Ticket, approval.ticket_id)
        if ticket:
            decline_stage(ticket, approval, f"Ignored in the inbox: {cleaned}")
    logger.info("Approval %s ignored (%s): %s", approval.id, effect.value, cleaned)
    event_bus.publish(
        session,
        EventType.APPROVAL_RESOLVED,
        workspace_id=approval.workspace_id,
        ticket_id=approval.ticket_id,
        payload={"approval_id": approval.id, "approved": False, "ignored": True},
    )
    return approval


def denial_message(approval: Approval) -> str:
    """What a waiting agent is told when its prompt was rejected or ignored."""
    if approval.status is ApprovalStatus.IGNORED:
        reason = json.loads(approval.response_json or "{}").get("ignored_reason", "")
        return (
            f"Ignored in Loregarden approval inbox: {reason}"
            if reason
            else ("Ignored in Loregarden approval inbox")
        )
    return "Rejected in Loregarden approval inbox"
