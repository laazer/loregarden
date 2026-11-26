"""A human gate closes itself when its review kit says nothing is a person's to judge.

The stage before a human gate owes it a review kit (`gate_prep`): either steps
a person judges, or ``nothing_to_judge``, saying what proved every claim the
change makes. On 2026-11-26 three blobert playtest gates (138, 140, 150) sat in
the inbox with that second kind: a person opening one was told there was
nothing for them to do, and had to sign it anyway.

Narrow, like the other signers in `autopilot_sign_off.signer_for_gate`:

- only the legacy "approve this stage" sign-off, never an authored question,
  an authority grant or a recheck, and never a gate a criterion settled as a
  person's judgment waits at (the caller checks both first);
- only when every kit on the ticket parses: an unreadable newer kit may have
  been the one asking for a judgment.
"""

from __future__ import annotations

from loregarden.models.domain import Approval, OrchestratorDecision, Ticket
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.review_kit import review_kits_for
from sqlmodel import Session

#: `Approval.resolved_by` for a gate closed because nothing was left to judge.
RESOLVED_BY_NOTHING_TO_JUDGE = "nothing_to_judge"


def nothing_to_judge(session: Session, ticket: Ticket) -> str:
    """What the ticket's kit says proved the change, or "" when a person judges it."""
    kit = review_kits_for(session, [ticket.id]).get(ticket.id)
    if kit is None or kit.unreadable:
        return ""
    return kit.nothing_to_judge


def record_unjudged_sign_off(
    session: Session, ticket: Ticket, approval: Approval, proof: str
) -> None:
    """Say on the ticket's history that its kit, not a person, closed the gate."""
    approval.resolved_by = RESOLVED_BY_NOTHING_TO_JUDGE
    session.add(approval)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.SIGNED_OFF_NOTHING_TO_JUDGE,
        stage_key=approval.stage_key,
        reason=(
            f"Closed the '{approval.stage_key}' gate without a person: its review kit says "
            f"nothing in this change is a person's to judge. What proved it: {proof}"
        ),
        evidence={"approval_id": approval.id},
    )
    session.commit()


__all__ = ["RESOLVED_BY_NOTHING_TO_JUDGE", "nothing_to_judge", "record_unjudged_sign_off"]
