"""Finish a ticket parked on its terminal stage, because nothing else will.

Every other recovery path in this control plane works by dispatching an agent
on the stage that stopped: the repair turn re-arms it under the repair agent,
a requeue sets it PENDING for its own agent, a scope reroute pins a sibling.
The terminal stage has no agent, so none of them apply to it — and the one
thing that *does* run it, `finalize_workflow` from the orchestrator loop, is
reached only while an orchestration is alive.

That leaves a hole with no floor. `_finish_workflow` lands the work before it
marks the terminal stage done, and a failed landing blocks from there (768).
The orchestration finishes BLOCKED in the same second. When the landing's
cause is then cleared — the conflict resolved by hand, the target's ref put
back — clearing the block returns the terminal stage to PENDING, which is a
state only an orchestration loop consumes, and there is no longer a loop. The
ticket reads `in_progress`, with no blocking text, on a stage no dispatcher
will ever select: alive-looking and dead.

lg-initiatives-cross-754 sat exactly that way for three hours after its
conflict was resolved, and would have sat there indefinitely.

So the sweep retries the finish. It selects only tickets nothing live can
account for — no agent run, no orchestration — and hands each to
`finalize_workflow`, which is the same call the orchestrator would make. If
the landing now succeeds the ticket lands and derives done. If it still fails
it blocks again *with git's own reason*, which is visible, and a BLOCKED stage
is no longer PENDING, so the sweep does not select it twice.
"""

from __future__ import annotations

import logging

from loregarden.core.workflow_terminal import find_terminal_stage
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestratorDecision,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.land_ticket import landing_would_succeed
from loregarden.services.landing import parked_on_failed_landing
from loregarden.services.orchestration import (
    LIVE_ORCHESTRATION_STATUSES,
    OrchestrationService,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: A ticket in one of these has been decided; finishing it would overwrite the
#: decision. PARKED joins the terminal two for the reason `queue_repair` gives:
#: someone set it aside on purpose.
_SETTLED_STATES: tuple[TicketState, ...] = (
    TicketState.DONE,
    TicketState.WONT_DO,
    TicketState.PARKED,
)

_LIVE_RUN_STATUSES: tuple[RunStatus, ...] = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.AWAITING_PERMISSION,
)


def _parked_on_terminal_stage(session: Session, ticket: Ticket) -> bool:
    """Whether this ticket's cursor sits on its own terminal stage, unfinished.

    The ticket row's `workflow_stage_status` describes the stage the cursor
    *left*, not the one it is on, so the stage map is the authority — the same
    reason `_finish_workflow` reads it rather than the row.
    """
    _, stages = resolve_ticket_stages(session, ticket)
    if not stages:
        return False
    terminal = find_terminal_stage(stages)
    if terminal is None or ticket.workflow_stage_key != terminal.key:
        return False
    instance, _ = OrchestrationService(session).ensure_workflow_instance(ticket, commit=False)
    if instance is None:
        return False
    return parse_stage_map(instance, stages).get(terminal.key) is StageStatus.PENDING


def _has_children(session: Session, ticket: Ticket) -> bool:
    return (
        session.exec(select(Ticket.id).where(Ticket.parent_ticket_id == ticket.id).limit(1)).first()
        is not None
    )


def finish_parked_terminal_tickets(
    session: Session, *, ticket_id: str | None = None
) -> list[Ticket]:
    """Finish every ticket parked on its terminal stage. Returns the ones tried.

    `ticket_id` narrows the sweep to one ticket, for a test or an operator
    asking about a specific one.
    """
    live_run_tickets = select(AgentRun.ticket_id).where(
        col(AgentRun.status).in_(_LIVE_RUN_STATUSES)
    )
    live_orch_tickets = select(OrchestrationRun.ticket_id).where(
        col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES)
    )
    query = select(Ticket).where(
        Ticket.workflow_stage_status == StageStatus.PENDING,
        col(Ticket.state).not_in(_SETTLED_STATES),
        # An operator who set the state by hand has decided, and `state_locked`
        # is how that is recorded; finishing past it would overwrite them.
        col(Ticket.state_locked).is_(False),
        col(Ticket.id).not_in(live_run_tickets),
        col(Ticket.id).not_in(live_orch_tickets),
    )
    if ticket_id:
        query = query.where(Ticket.id == ticket_id)

    orch = OrchestrationService(session)
    finished: list[Ticket] = []
    for ticket in session.exec(query).all():
        if not _parked_on_terminal_stage(session, ticket):
            continue
        # A parent's terminal stage summarises its children rather than landing
        # work of its own; that is `_finalize_aggregator_parent`'s, and it needs
        # the orchestration run this sweep does not have.
        if _has_children(session, ticket):
            continue
        stage_key = ticket.workflow_stage_key
        try:
            orch.finalize_workflow(ticket)
        except ValueError:
            # Not silent: the ticket stays parked and the next sweep will try
            # again, so the reason has to be readable now.
            logger.warning(
                "Could not finish ticket %s parked on its terminal stage",
                ticket.external_id,
                exc_info=True,
            )
            session.rollback()
            continue
        record_orchestrator_decision(
            session,
            ticket,
            decision=OrchestratorDecision.FINISHED_PARKED_TERMINAL_STAGE,
            stage_key=stage_key,
            reason=(
                f"Ticket sat on terminal stage '{stage_key}' with no orchestration to finish "
                f"it; retried the finish, which ended {ticket.state.value}."
            ),
            evidence={"state": ticket.state.value, "landed_sha": ticket.landed_sha},
        )
        finished.append(ticket)
    if finished:
        logger.warning(
            "Finished %d ticket(s) parked on a terminal stage: %s",
            len(finished),
            ", ".join(t.external_id for t in finished),
        )
    return finished


def finish_tickets_whose_landing_cleared(
    session: Session, *, ticket_id: str | None = None
) -> list[Ticket]:
    """Finish tickets blocked on their terminal stage whose landing would now succeed.

    The sweep above leaves a BLOCKED terminal stage alone on purpose: a
    landing that conflicts would conflict again every pass. But the block
    outlives its cause. 782's conflict was resolved and landed by hand, and the
    ticket stayed blocked until someone requeued it and started an
    orchestration with nothing to do but notice (lg-milestone-that-928).

    So this asks git, read-only, whether the landing would succeed now: the
    branch already contained in its target, or merging cleanly into it. Only
    then is the ticket handed to `finalize_workflow`, which lands and derives
    done. A landing that would still conflict is not selected again, so this
    retries nothing that has not changed.
    """
    live_run_tickets = select(AgentRun.ticket_id).where(
        col(AgentRun.status).in_(_LIVE_RUN_STATUSES)
    )
    live_orch_tickets = select(OrchestrationRun.ticket_id).where(
        col(OrchestrationRun.status).in_(LIVE_ORCHESTRATION_STATUSES)
    )
    query = select(Ticket).where(
        Ticket.state == TicketState.BLOCKED,
        col(Ticket.state_locked).is_(False),
        col(Ticket.id).not_in(live_run_tickets),
        col(Ticket.id).not_in(live_orch_tickets),
    )
    if ticket_id:
        query = query.where(Ticket.id == ticket_id)

    orch = OrchestrationService(session)
    finished: list[Ticket] = []
    for ticket in session.exec(query).all():
        workspace = session.get(Workspace, ticket.workspace_id)
        if (
            workspace is None
            or not parked_on_failed_landing(session, ticket)
            or _has_children(session, ticket)
            or not landing_would_succeed(session, ticket, workspace)
        ):
            continue
        stage_key = ticket.workflow_stage_key
        try:
            orch.finalize_workflow(ticket)
        except ValueError:
            logger.warning(
                "Could not finish ticket %s whose landing cleared",
                ticket.external_id,
                exc_info=True,
            )
            session.rollback()
            continue
        record_orchestrator_decision(
            session,
            ticket,
            decision=OrchestratorDecision.FINISHED_CLEARED_LANDING,
            stage_key=stage_key,
            reason=(
                f"Ticket was blocked on terminal stage '{stage_key}' by its landing, and the "
                f"landing now succeeds; finished it, which ended {ticket.state.value}."
            ),
            evidence={"state": ticket.state.value, "landed_sha": ticket.landed_sha},
        )
        finished.append(ticket)
    if finished:
        logger.warning(
            "Finished %d ticket(s) whose landing block had cleared: %s",
            len(finished),
            ", ".join(t.external_id for t in finished),
        )
    return finished
