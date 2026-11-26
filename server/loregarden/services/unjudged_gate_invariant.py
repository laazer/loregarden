"""Invariant: no human gate waits on a person when its review kit says nothing is theirs to judge.

`signer_for_gate` closes such a gate when an orchestration run parks at it.
A gate opened any other way (a stage finished from a terminal, an MCP
`request_approval`), or one whose kit arrived after it opened, waits for this
pass. Kept apart from `unjudged_gates`, which `autopilot_sign_off` imports:
the repair resolves through `ApprovalService`, which reaches the orchestrator.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    InvariantReentry,
    Ticket,
)
from loregarden.services.autopilot_sign_off import automation_may_sign
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.orchestration import ApprovalService
from loregarden.services.unjudged_gates import nothing_to_judge, record_unjudged_sign_off
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def _unjudged(session: Session) -> list[tuple[Approval, Ticket, str]]:
    pending = session.exec(
        select(Approval).where(
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
            Approval.status == ApprovalStatus.PENDING,
            col(Approval.ticket_id).is_not(None),
        )
    ).all()
    found: list[tuple[Approval, Ticket, str]] = []
    for approval in pending:
        ticket = session.get(Ticket, approval.ticket_id)
        if ticket is None or not automation_may_sign(session, ticket, approval):
            continue
        proof = nothing_to_judge(session, ticket)
        if proof:
            found.append((approval, ticket, proof))
    return found


def unjudged_gates(session: Session) -> list[Violation]:
    """Pending stage sign-offs whose ticket's kit says nothing is a person's to judge.

    `entered_at` is the gate's `created_at`; a kit attached after the gate
    opened entered the state later than that, which errs towards reporting.
    """
    return [
        Violation(
            subject_id=approval.id,
            ticket_id=ticket.id,
            entered_at=approval.created_at,
            detail=(
                f"{ticket.external_id} waits at '{approval.stage_key}' for a person, and its "
                "review kit says nothing there is a person's to judge"
            ),
        )
        for approval, ticket, _proof in _unjudged(session)
    ]


def close_unjudged_gates(
    session: Session,
    violations: Sequence[Violation],  # noqa: ARG001 - re-read: the world may have moved
) -> int:
    """Approve each such gate as a person would, and say on its history why nobody did."""
    closed = 0
    for approval, ticket, proof in _unjudged(session):
        try:
            ApprovalService(session).resolve(approval.id, approved=True)
        except ValueError:
            # The approve path refused (unpublished work, a stale park, a closed
            # ticket): the gate stays in the inbox for a person, who sees why there.
            logger.warning(
                "Left %s's '%s' gate for a person: approving it was refused",
                ticket.external_id,
                approval.stage_key,
                exc_info=True,
            )
            session.rollback()
            continue
        resolved = session.get(Approval, approval.id)
        if resolved is not None:
            record_unjudged_sign_off(session, ticket, resolved, proof)
        closed += 1
    return closed


UNJUDGED_GATES_CLOSE = Invariant(
    name="unjudged_gates_close",
    ticket="lg-gate-studio-2810",
    statement="no stage sign-off waits on a person when the ticket's review kit says nothing is theirs to judge",
    detect=unjudged_gates,
    repair=close_unjudged_gates,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 11, 26),
    expected_because=(
        "a gate opened outside an orchestration run, or a kit attached after the gate "
        "opened, is never shown to `signer_for_gate`; nothing else reads the kit again"
    ),
)
