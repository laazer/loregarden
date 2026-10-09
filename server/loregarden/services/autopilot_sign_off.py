"""The initiative autopilot signs off legacy stage gates on the work it started.

`legacy-stage-sign-off` is what a pre-exit-actions `gate_required: true` became
(`migrations_exit_actions`): an operator-judgment "approve this stage" with no
particular question behind it. On a ticket the initiative autopilot dispatched,
while the autopilot is still on, the operator has already said who drives — so
the autopilot answers that gate itself, the way `approve_design_plans` answers a
design plan's, and says so in the ticket history and in its own log.

Deliberately narrow. Every outstanding action on the gate must be the legacy
sign-off: a gate authored as a real question, an authority grant or a recheck
stays a person's (AC-6). Turning the autopilot off hands these gates back.
"""

from __future__ import annotations

from loregarden.models.domain import (
    Approval,
    AutopilotAction,
    AutopilotEvent,
    InitiativePlan,
    OrchestratorDecision,
    Ticket,
)
from loregarden.services.exit_action_ledger import (
    gate_is_operator_judgment_only,
    read_gate_resolution,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from sqlmodel import Session, col, select

#: The action key `migrations_exit_actions.legacy_sign_off_action` writes.
LEGACY_SIGN_OFF_KEY = "legacy-stage-sign-off"

#: `Approval.resolved_by` for a gate the autopilot signed.
RESOLVED_BY_AUTOPILOT = "autopilot"


def _gate_is_legacy_sign_off_only(approval: Approval) -> bool:
    # The judgment-only check first: it reports an unreadable gate rather than raising.
    if not gate_is_operator_judgment_only(approval):
        return False
    actions = read_gate_resolution(approval).human_required_actions
    return all(action.action_key == LEGACY_SIGN_OFF_KEY for action in actions)


def self_and_ancestors(session: Session, ticket: Ticket) -> list[str]:
    """This ticket's id and its ancestors', nearest first; a cycle stops the walk."""
    ids = [ticket.id]
    parent_id = ticket.parent_ticket_id
    while parent_id and parent_id not in ids:
        ids.append(parent_id)
        parent = session.get(Ticket, parent_id)
        parent_id = parent.parent_ticket_id if parent else None
    return ids


def autopilot_driving(session: Session, ticket: Ticket) -> InitiativePlan | None:
    """The plan whose autopilot is on and dispatched this ticket or an ancestor of it.

    An ancestor counts because the autopilot dispatches a parent and its
    orchestration runs the children: lg-run-durability-679 ran test-design
    under its parent 469, which was the ticket the autopilot queued.
    """
    return session.exec(
        select(InitiativePlan)
        .join(
            AutopilotEvent, col(AutopilotEvent.initiative_id) == col(InitiativePlan.initiative_id)
        )
        .where(
            col(AutopilotEvent.ticket_id).in_(self_and_ancestors(session, ticket)),
            AutopilotEvent.action == AutopilotAction.DISPATCHED,
            col(InitiativePlan.autopilot).is_(True),
        )
    ).first()


def autopilot_may_sign_off(
    session: Session, ticket: Ticket, approval: Approval
) -> InitiativePlan | None:
    """The plan that signs this gate off, or None when it waits for a person."""
    if not _gate_is_legacy_sign_off_only(approval):
        return None
    return autopilot_driving(session, ticket)


def record_autopilot_sign_off(
    session: Session, ticket: Ticket, plan: InitiativePlan, approval: Approval
) -> None:
    """Say on the ticket and in the autopilot log that the autopilot, not a person, approved."""
    approval.resolved_by = RESOLVED_BY_AUTOPILOT
    session.add(approval)
    session.add(
        AutopilotEvent(
            initiative_id=plan.initiative_id,
            action=AutopilotAction.SIGNED_OFF,
            ticket_id=ticket.id,
            detail=f"approved the '{approval.stage_key}' stage gate",
        )
    )
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.APPROVED_LEGACY_SIGN_OFF,
        stage_key=approval.stage_key,
        reason=(
            f"The initiative autopilot approved the '{approval.stage_key}' stage gate on work "
            "it started. Turn the autopilot off to approve these gates yourself."
        ),
        evidence={"initiative_id": plan.initiative_id, "approval_id": approval.id},
    )
    session.commit()
