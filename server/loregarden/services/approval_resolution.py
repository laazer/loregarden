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

from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, StageStatus, Ticket
from loregarden.services.sibling_overlap_resolution import (
    is_sibling_overlap_park,
    wait_for_siblings,
)
from loregarden.services.ticket_dependencies import DependencyCycleError
from loregarden.services.ticket_state_service import RESOLVED_STATES
from loregarden.services.unresolved_ledger import (
    HUMAN_AGENT_ID,
    LedgerActor,
    persisted_open_names,
    waive,
)
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session

if TYPE_CHECKING:
    # Only as a type: `orchestration` imports this module, and the appliers
    # need nothing from the service at runtime but two resolvers on it.
    from loregarden.services.orchestration import OrchestrationService

logger = logging.getLogger(__name__)

#: The kinds whose answer re-runs a stage (`ApprovalService._apply_ticket_resolution`
#: dispatches each): a park re-dispatches it, a decision or an operator's
#: override requeues it. A gate or rework pause is not here: answering a stale one
#: on a closed ticket marks or routes a stage and moves nothing, and operators do
#: clear them that way.
STAGE_RERUNNING_APPROVAL_KINDS = frozenset(
    {
        ApprovalKind.STAGE_PARK,
        ApprovalKind.BLOCK_DECISION,
        ApprovalKind.OPERATOR_QUESTION,
    }
)


def refuse_rerunning_a_closed_ticket(session: Session, approval: Approval) -> None:
    """Refuse, before anything is recorded, an answer that would re-run a closed ticket's stage.

    The answer's own effect runs after the approval row commits, so a ticket
    that cannot take it (`done -> in_progress` is not a chosen move) used to fail
    the request with the answer already stored: the card read "Overridden" beside
    a "Resolve approval failed" toast (lg-conversation-that-2733). A `wont_do`
    ticket did take it, and was silently revived. Ignore stays open: it closes
    the card without moving the ticket.
    """
    if approval.kind not in STAGE_RERUNNING_APPROVAL_KINDS or not approval.ticket_id:
        return
    ticket = session.get(Ticket, approval.ticket_id)
    if ticket is None or ticket.state not in RESOLVED_STATES:
        return
    raise ValueError(
        f"{ticket.external_id} is {ticket.state.value}, so this answer has no stage to act on. "
        "Reopen the ticket (move it to backlog) to act on it, or ignore the question to close it."
    )


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

    Rejecting a sibling-overlap park is "wait" instead: the ticket is linked to
    wait for the siblings and the stage goes back to PENDING, where the
    dependency hold keeps it until they land (`services.sibling_overlap_park`).
    """
    instance, stages = orchestration._resolve_stages(ticket)
    if not instance or not stages or not approval.stage_key:
        return False

    resume = approved
    if approved:
        set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.PENDING)
        ticket.dispatch_waiver_stage_key = approval.stage_key
        ticket.dispatch_waiver_approval_id = approval.id
        ticket.blocking_issues = ""
    else:
        waits, refusal = _wait_for_siblings_instead(session, ticket, approval)
        if waits:
            set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.PENDING)
            ticket.blocking_issues = ""
            resume = True
        else:
            set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.BLOCKED)
            ticket.blocking_issues = refusal or response_text.strip() or approval.impact

    session.add(ticket)
    session.add(instance)
    session.commit()
    return resume


def _wait_for_siblings_instead(
    session: Session, ticket: Ticket, approval: Approval
) -> tuple[bool, str]:
    """Whether a rejected park made ``ticket`` wait for its siblings, and why not if it could not.

    Only a *rejected* overlap park waits. An ignored one is a person dismissing
    a stale card, and linking edges on their behalf would order work nobody chose.
    """
    if not is_sibling_overlap_park(approval) or approval.status is not ApprovalStatus.REJECTED:
        return False, ""
    try:
        wait_for_siblings(session, ticket, approval)
    except DependencyCycleError as exc:
        logger.warning("could not make %s wait for its siblings: %s", ticket.external_id, exc)
        return False, f"Could not wait for the overlapping siblings: {exc}"
    return True, ""


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
            reason = response_text.strip()
            open_names = persisted_open_names(session, ticket)
            if reason:
                human = LedgerActor(agent_id=HUMAN_AGENT_ID, stage_key="")
                for name in open_names:
                    waive(session, ticket, name, reason, human)
            elif open_names:
                set_stage_status(ticket, instance, stages, approval.stage_key, StageStatus.PENDING)
                session.add(ticket)
                session.add(instance)
                session.commit()
                return True
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
