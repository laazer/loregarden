"""A landing resolver armed where no orchestration runs gets one, or a block.

`landing._offer_conflict_resolution` arms a resolver by leaving the terminal
stage PENDING under the repair agent's pin, a state only an orchestration's
loop consumes. A landing also runs where there is none (the terminal stage
sweep, the land tool), and an orchestration can arm one and end before its
loop dispatches it (cancelled, lease expired, stage budget spent). Before this
nothing consumed such a pin: the next sweep landed, conflicted and armed
again, every ~90s, never spending an attempt. Six tickets looped that way on
2026-11-27.

`serve_stranded_resolver` starts an orchestration for the pin, the way CI
autofix starts one for its own (`ci_autofix_dispatch`). The sweep calls it
right after a landing it ran arms one; `LANDING_RESOLVERS_HAVE_A_LOOP` calls
it for every other path. Two bounds keep it from looping in turn: each start
dispatches the pinned resolver first, which spends one of
`max_conflict_resolve_attempts` (the landing blocks when they are gone), and
no start follows an orchestration that ended having done nothing. That case,
and an admission that refuses, block with the reason instead.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.core.workflow_terminal import find_terminal_stage
from loregarden.models.domain import (
    AgentRun,
    InvariantReentry,
    OrchestrationRun,
    OrchestratorDecision,
    QueuedRun,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.idle_orchestrations import did_nothing
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.landing import block_stranded_resolver
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_recovery import (
    InterruptionResume,
    admit_resumes,
    latest_orchestration_run,
    schedule_interrupted_resumes,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.parallel_queue import (
    LIVE_ORCHESTRATION_STATUSES,
    LIVE_RUN_STATUSES,
    WAITING_STATUSES,
)
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

_SETTLED_STATES: tuple[TicketState, ...] = (
    TicketState.DONE,
    TicketState.WONT_DO,
    TicketState.PARKED,
)


def _tickets_with_a_loop():
    """Ticket ids something will still run: a live orchestration or agent run, or a waiting lane entry."""
    return (
        select(OrchestrationRun.ticket_id)
        .where(col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES))
        .union(
            select(AgentRun.ticket_id).where(col(AgentRun.status).in_(LIVE_RUN_STATUSES)),
            select(QueuedRun.ticket_id).where(col(QueuedRun.status).in_(WAITING_STATUSES)),
        )
    )


def stranded_resolver_tickets(session: Session, *, ticket_id: str | None = None) -> list[Ticket]:
    """Tickets whose terminal stage is PENDING under the repair pin with nothing to run it."""
    query = (
        select(Ticket)
        .where(Ticket.next_agent == REPAIR_AGENT_ID)
        .where(Ticket.workflow_stage_status == StageStatus.PENDING)
        .where(col(Ticket.state).not_in(_SETTLED_STATES))
        .where(col(Ticket.id).not_in(_tickets_with_a_loop()))
    )
    if ticket_id:
        query = query.where(Ticket.id == ticket_id)
    orch = OrchestrationService(session)
    stranded: list[Ticket] = []
    for ticket in session.exec(query).all():
        _, stages = resolve_ticket_stages(session, ticket)
        terminal = find_terminal_stage(stages) if stages else None
        if terminal is None or ticket.workflow_stage_key != terminal.key:
            continue
        instance = orch.get_workflow_instance(ticket.id)
        if instance is not None and parse_stage_map(instance, stages).get(terminal.key) is (
            StageStatus.PENDING
        ):
            stranded.append(ticket)
    return stranded


def _start(session: Session, ticket: Ticket) -> str:
    """Start (or queue) an orchestration to run the pin. "" when one will; otherwise why not."""
    previous = latest_orchestration_run(session, ticket)
    if previous is not None and did_nothing(session, previous):
        return (
            f"the last orchestration ({previous.run_code}) ended having started nothing, "
            "so another would likely do the same"
        )
    stage_key = ticket.workflow_stage_key
    plan = InterruptionResume(
        ticket_id=ticket.id,
        # Carried from whoever drove the ticket last, as CI autofix does: a
        # ticket an operator approved step by step is not promoted to unattended.
        auto_approve=previous.auto_approve if previous else False,
        approve_design_plans=previous.approve_design_plans if previous else True,
        auto_repair=previous.auto_repair if previous else True,
        stop_at_stage_key=stage_key,
    )
    session.commit()
    requests, handled = admit_resumes(session, [(ticket, plan)])
    if ticket.id not in handled:
        return "the queue admitted no orchestration for it"
    session.commit()
    schedule_interrupted_resumes(requests)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.DISPATCHED_LANDING_RESOLVER,
        stage_key=stage_key,
        reason=(
            f"A landing resolver was armed on '{stage_key}' with no orchestration to run it; "
            f"{'started' if requests else 'queued'} one."
        ),
        evidence={"previous_run": previous.run_code if previous else None},
    )
    return ""


def serve_stranded_resolver(session: Session, ticket: Ticket) -> bool:
    """Start an orchestration for a stranded resolver pin, or block. True when one was started."""
    why = _start(session, ticket)
    if not why:
        return True
    instance = OrchestrationService(session).get_workflow_instance(ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    if instance is None:
        logger.warning(
            "%s has a stranded landing resolver (%s) and no workflow instance to block",
            ticket.external_id,
            why,
        )
        return False
    block_stranded_resolver(session, ticket, instance, stages, ticket.workflow_stage_key, why)
    session.commit()
    return False


def _detect(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=None,
            detail=(
                f"{ticket.external_id}: a landing resolver is armed on "
                f"'{ticket.workflow_stage_key}' and nothing will run it"
            ),
        )
        for ticket in stranded_resolver_tickets(session)
    ]


def _repair(session: Session, violations: Sequence[Violation]) -> int:
    # Re-detect: an orchestration may have started for the ticket since.
    wanted = {v.subject_id for v in violations}
    served = 0
    for ticket in stranded_resolver_tickets(session):
        if ticket.id in wanted:
            serve_stranded_resolver(session, ticket)
            served += 1
    return served


LANDING_RESOLVERS_HAVE_A_LOOP = Invariant(
    name="landing_resolvers_have_a_loop",
    ticket="lg-code-knowledge-368",
    statement="no landing resolver turn is armed on a ticket nothing will run",
    detect=_detect,
    repair=_repair,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 11, 27),
    expected_because=(
        "a landing arms a resolver wherever it runs, including the land tool and an "
        "orchestration that then ends before dispatching it; the sweep serves its own "
        "arming at once, and this serves the rest"
    ),
)
