"""Write an approval's outcome into the workflow.

Two shapes, and they are opposites. A gate asks "is this stage's work good?",
so approving marks the stage DONE (or reroutes it with rework). A park asks
"should this stage start at all, given the machine it would run on?", so
approving sends it back to PENDING to actually run. Each returns whether the
orchestration should resume; deciding *that* — and scheduling it — stays with
`ApprovalService`, which owns the approval's lifecycle.

Lifted out of `orchestration.py` when the landing hook (768) pushed that
module past its size cap; the appliers were the cohesive piece that needed
nothing from the service but its session and its orchestration.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from loregarden.models.domain import Approval, ApprovalKind, StageStatus, Ticket
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session

if TYPE_CHECKING:
    # Only as a type: `orchestration` imports this module, and the appliers
    # need nothing from the service at runtime but two resolvers on it.
    from loregarden.services.orchestration import OrchestrationService

logger = logging.getLogger(__name__)


def apply_park_resolution(
    session: Session,
    orchestration: OrchestrationService,
    ticket: Ticket,
    approval: Approval,
    *,
    approved: bool,
    response_text: str,
) -> bool:
    """Resolve a parked stage. True when the orchestration should resume.

    Routed through `apply_gate_resolution` — as it was until this was fixed — a
    click meant to unstick a broken checkout marked the stage complete without
    it ever running, and the workflow advanced past it.

    Approving arms the one-shot waiver `_consume_dispatch_waiver` spends at the
    next dispatch. Without it the stage re-dispatches, meets the same failing
    check, and parks again.

    Rejecting blocks in place and does NOT reroute. The reject route exists to
    send unsatisfactory *work* back to an earlier stage; a `core.bare` checkout
    is not a defect in the plan, and re-running the planner would fix nothing
    while consuming a rework round.
    """
    instance, stages = orchestration._resolve_stages(ticket)
    if not instance or not stages or not approval.stage_key:
        return False

    if approved:
        set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.PENDING)
        ticket.dispatch_waiver_stage_key = approval.stage_key
        ticket.dispatch_waiver_approval_id = approval.id
        ticket.blocking_issues = ""
    else:
        set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.BLOCKED)
        ticket.blocking_issues = response_text.strip() or approval.impact

    session.add(ticket)
    session.add(instance)
    session.commit()
    return approved


def apply_gate_resolution(
    session: Session,
    orchestration: OrchestrationService,
    ticket: Ticket,
    approval: Approval,
    *,
    approved: bool,
    rework_route_key: str,
    response_text: str,
    settle_rework_pause: Callable[[Ticket, Approval], None],
) -> bool:
    """Resolve a gate. True when the orchestration should resume.

    ``settle_rework_pause`` is called for a REWORK_PAUSE approval before the
    workflow is touched — giving the pause its loop budget back is the
    approval's own bookkeeping, and stays with the caller.
    """
    instance, stages = orchestration._resolve_stages(ticket)
    if not instance or not stages or not approval.stage_key:
        return False

    if approval.kind is ApprovalKind.REWORK_PAUSE:
        settle_rework_pause(ticket, approval)

    if approved and rework_route_key:
        note = response_text.strip() or (
            "Formalize the prototype changes made during this verification "
            "with production-quality implementation and tests."
        )
        transitions = orchestration._resolve_transitions(ticket)
        apply_stage_route(
            ticket,
            instance,
            stages,
            transitions,
            from_key=approval.stage_key,
            outcome="reject",
            next_stage_key=rework_route_key,
            blocking_issues=f"'{approval.stage_key}' gate approved with rework: {note}",
        )
    elif approved:
        if approval.kind is ApprovalKind.REWORK_PAUSE:
            # Cleared BEFORE the status write, not after: `set_stage_status`
            # reconciles ticket.state on the way out, and
            # `_derive_ticket_state` returns BLOCKED for any ticket whose
            # `blocking_issues` is set while the cursor sits on a
            # BLOCKED/RUNNING/AWAITING stage. Clearing afterwards leaves the
            # state derived from a pause the operator has just resolved —
            # invisible when the next stage happens to be PENDING, and wrong
            # the moment it is a gate.
            ticket.blocking_issues = ""
        set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.DONE)
    else:
        reject_message = response_text.strip() or "Human rejected approval"
        transitions = orchestration._resolve_transitions(ticket)
        try:
            apply_stage_route(
                ticket,
                instance,
                stages,
                transitions,
                from_key=approval.stage_key,
                outcome="reject",
                next_stage_key=rework_route_key,
                blocking_issues=reject_message,
            )
        except ValueError:
            # No reject transition and no preceding stage to fall back to
            # (already first-in-order) — hard-block in place.
            logger.warning(
                "No rework route from stage %s on ticket %s; blocking in place",
                approval.stage_key,
                ticket.external_id,
                exc_info=True,
            )
            ticket.blocking_issues = reject_message
            set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.BLOCKED)

    session.add(ticket)
    session.add(instance)
    session.commit()
    return approved
