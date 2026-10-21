"""Resume a ticket the moment the tickets it waited on have landed.

Holds are stateless (`dependency_hold`): every dispatch asks again. That is
enough for a ticket something will dispatch again anyway — the initiative
autopilot re-reads its plan each tick — but not for one whose drive paused on
the hold. Before this, nothing read a prerequisite's completion: a ticket held
mid-workflow sat paused, and lg-milestone-that-1315, parked by hand on its
prerequisite, would have stayed parked after 1314 landed.

Two kinds of ticket are released, on every reconcile pass:

- **Held.** Open, held since its last run, and nothing holds it now. Its
  stage is requeued and its drive resumed the way it was being driven
  (`requeue_and_plan_resume`, the primitive usage-limit waits use).
- **Parked on prerequisites.** A person parks a ticket for many reasons, and
  only one of them clears by itself. A parked ticket seen waiting on unmet
  prerequisites is tagged `WAITING_TAG`; once a tagged ticket has no
  prerequisite left unmet, it is released — resumed if it ever ran, back to
  the backlog if it never did. A parked ticket never seen waiting stays parked.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    EventType,
    OrchestrationRunStatus,
    OrchestratorDecision,
    Ticket,
    TicketDependency,
    TicketState,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.dependency_hold import (
    holding_prerequisites,
    latest_hold_at,
    ran_since,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_recovery import (
    InterruptionResume,
    admit_resumes,
    latest_orchestration_run,
    requeue_and_plan_resume,
    schedule_interrupted_resumes,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.requeue import requeue_stage
from loregarden.services.run_concurrency import find_active_orchestration_run
from loregarden.services.ticket_tags import load_tags, serialize_tags
from sqlalchemy import func
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: On a parked ticket: it was seen waiting on unmet prerequisites, so landing
#: them is what it is parked for.
WAITING_TAG = "waiting-on-prerequisites"

_HELD_STATES = (TicketState.BACKLOG, TicketState.IN_PROGRESS)

RELEASE_REASON = "Every prerequisite it waited on is done and landed."


def _held_candidates(session: Session) -> list[Ticket]:
    """Open, unblocked tickets with a hold on their history.

    Backlog as well as in progress: a ticket held before any stage finished
    derives back to the backlog, and its hold said it starts when they land.
    """
    held_ids = select(DomainEvent.ticket_id).where(
        DomainEvent.type == EventType.ORCHESTRATOR_DECISION,
        func.json_extract(col(DomainEvent.payload_json), "$.decision")
        == OrchestratorDecision.HELD_ON_PREREQUISITES.value,
    )
    return list(
        session.exec(
            select(Ticket).where(col(Ticket.state).in_(_HELD_STATES), col(Ticket.id).in_(held_ids))
        ).all()
    )


def _parked_with_prerequisites(session: Session) -> list[Ticket]:
    has_edge = select(TicketDependency.ticket_id)
    return list(
        session.exec(
            select(Ticket).where(Ticket.state == TicketState.PARKED, col(Ticket.id).in_(has_edge))
        ).all()
    )


def _tagged_unparked(session: Session) -> list[Ticket]:
    return list(
        session.exec(
            select(Ticket).where(
                Ticket.state != TicketState.PARKED,
                col(Ticket.tags_json).contains(f'"{WAITING_TAG}"'),
            )
        ).all()
    )


def _set_waiting_tag(session: Session, ticket: Ticket, *, present: bool) -> bool:
    tags = load_tags(ticket.tags_json)
    if (WAITING_TAG in tags) == present:
        return False
    tags = [*tags, WAITING_TAG] if present else [tag for tag in tags if tag != WAITING_TAG]
    ticket.tags_json = serialize_tags(tags)
    ticket.revision += 1
    ticket.updated_at = utcnow()
    session.add(ticket)
    return True


def _record_release(session: Session, ticket: Ticket) -> None:
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.RELEASED_ON_PREREQUISITES,
        stage_key=ticket.workflow_stage_key,
        reason=f"Released {ticket.external_id}: {RELEASE_REASON}",
    )


def _ever_ran(session: Session, ticket_id: str) -> bool:
    return (
        session.exec(select(AgentRun.id).where(AgentRun.ticket_id == ticket_id)).first() is not None
    )


def _stopped_by_a_person(session: Session, ticket: Ticket) -> bool:
    """Its last drive was cancelled: resuming it would undo the stop."""
    previous = latest_orchestration_run(session, ticket)
    return previous is not None and (
        previous.cancel_requested_at is not None
        or previous.status is OrchestrationRunStatus.CANCELLED
    )


def _release_parked(session: Session, ticket: Ticket) -> InterruptionResume | None:
    """Release a parked ticket whose prerequisites have landed; its resume plan, if any."""
    _set_waiting_tag(session, ticket, present=False)
    _record_release(session, ticket)
    session.commit()
    plan = None
    if _ever_ran(session, ticket.id):
        plan = requeue_and_plan_resume(
            session, ticket, ticket.workflow_stage_key, reason=RELEASE_REASON
        )
    if plan is None and find_active_orchestration_run(session, ticket.id) is None:
        # Never ran, or its last drive was stopped by a person: back to the
        # backlog, where whatever picks up ready work finds it.
        requeue_stage(
            session,
            OrchestrationService(session),
            ticket,
            stage_key=ticket.workflow_stage_key,
            reason=RELEASE_REASON,
            actor="orchestrator",
            state=TicketState.BACKLOG,
        )
    return plan


def mark_parked_waits(session: Session) -> int:
    """Tag each parked ticket seen waiting on unmet prerequisites; untag the rest.

    Returns how many tags changed. A ticket a person moved out of parked keeps
    no tag: it would mean nothing there, and a later park for another reason
    must not inherit it.
    """
    changed = 0
    for ticket in _parked_with_prerequisites(session):
        if holding_prerequisites(session, ticket):
            changed += _set_waiting_tag(session, ticket, present=True)
    for ticket in _tagged_unparked(session):
        changed += _set_waiting_tag(session, ticket, present=False)
    session.commit()
    return changed


def _held_and_releasable(session: Session, ticket: Ticket) -> bool:
    held_at = latest_hold_at(session, ticket.id)
    if held_at is None or ran_since(session, ticket.id, held_at):
        return False
    if find_active_orchestration_run(session, ticket.id) is not None:
        return False
    return not _stopped_by_a_person(session, ticket) and not holding_prerequisites(session, ticket)


def _parked_and_releasable(session: Session, ticket: Ticket) -> bool:
    return WAITING_TAG in load_tags(ticket.tags_json) and not holding_prerequisites(session, ticket)


def releasable_tickets(session: Session) -> list[Ticket]:
    """Tickets waiting on prerequisites that have all landed, with nothing resuming them."""
    held = [t for t in _held_candidates(session) if _held_and_releasable(session, t)]
    parked = [t for t in _parked_with_prerequisites(session) if _parked_and_releasable(session, t)]
    return held + parked


def release_tickets(session: Session, tickets: list[Ticket]) -> list[str]:
    """Resume each of ``tickets`` that is still releasable. Returns the external ids.

    Re-checks each one: the world may have moved since it was selected.
    """
    released: list[str] = []
    plans: list[tuple[Ticket, InterruptionResume]] = []
    for ticket in tickets:
        session.refresh(ticket)
        if ticket.state is TicketState.PARKED and _parked_and_releasable(session, ticket):
            plan = _release_parked(session, ticket)
        elif ticket.state in _HELD_STATES and _held_and_releasable(session, ticket):
            _record_release(session, ticket)
            session.commit()
            plan = requeue_and_plan_resume(
                session, ticket, ticket.workflow_stage_key, reason=RELEASE_REASON
            )
        else:
            continue
        released.append(ticket.external_id)
        if plan is not None:
            plans.append((ticket, plan))
    requests, _ = admit_resumes(session, plans)
    schedule_interrupted_resumes(requests)
    if released:
        logger.warning(
            "Prerequisites landed; released %d ticket(s): %s", len(released), ", ".join(released)
        )
    return released
