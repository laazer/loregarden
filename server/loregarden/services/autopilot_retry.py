"""Which blocked tickets the autopilot may pick back up, and how it un-blocks one.

The plan graph reads every blocked ticket as BLOCKED, and the autopilot picks
only READY work, so a block a re-run could still clear sat until a person came
back to it. Two kinds of block qualify:

- **The queue's repair routes** (`queue_repair.resolve_repair_route`): the run
  was interrupted, a scope reroute is pinned, or the stage's run failed with
  dispatch budget left. A lane holds and retries these itself, but only for
  `REPAIR_ATTEMPT_CAP` attempts or `REPAIR_WALL_CLOCK`; one that blocked outside
  a lane, or outlived the hold, still has its route.
- **A block an agent can clear whose repair turn is unspent** (`block_repair`):
  a harness or work block on a stage that has not had its one repair turn,
  because the run had auto-repair off or the block was recorded outside an
  orchestration. The autopilot's own runs have auto-repair on, so if the re-run
  blocks the same way, that block is offered the repair turn.

Never a ticket whose state a person set, one waiting on a person's answer, or
one the retry breaker stopped. The re-run is charged to the stage's existing
budget: un-blocking (`queue_repair.unblock_for_rerun`) touches no counter and
writes no "Requeued" record, the mark `repair_already_spent` reads as a person's
requeue. So the stage's own caps keep binding. The autopilot also retries a
ticket at most once each time it is turned on.
"""

from __future__ import annotations

from datetime import datetime

from loregarden.models.domain import (
    AutopilotAction,
    AutopilotEvent,
    RepairRoute,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.block_repair import REPAIRABLE_KINDS, repair_already_spent
from loregarden.services.queue_repair import awaiting_a_person, resolve_repair_route
from loregarden.services.stage_retry_budget import (
    blocked_on_stage_retry_budget,
    resolve_stage_retry_budget,
    stage_retry_budget_state,
)
from sqlmodel import Session, select

_ROUTE_REASON = {
    RepairRoute.INTERRUPTION: "its last run was interrupted",
    RepairRoute.SCOPE_REROUTE: "a scope reroute is waiting to run",
    RepairRoute.STAGE_RETRY: "its stage run failed with retry budget left",
}


def retry_reason(session: Session, ticket: Ticket) -> str | None:
    """Why a re-run of this blocked ticket could still clear its block, or None."""
    if ticket.state != TicketState.BLOCKED or ticket.state_locked:
        return None
    route = resolve_repair_route(session, ticket)
    if route is not None:
        return _ROUTE_REASON[route]
    stage_key = ticket.workflow_stage_key
    if (
        ticket.block_kind not in REPAIRABLE_KINDS
        or not stage_key
        or ticket.workflow_stage_status != StageStatus.BLOCKED
        or awaiting_a_person(session, ticket)
        or blocked_on_stage_retry_budget(session, ticket, stage_key)
        or repair_already_spent(session, ticket, stage_key)
    ):
        return None
    budget = stage_retry_budget_state(
        session, ticket, stage_key, resolve_stage_retry_budget(session, ticket)
    )
    if budget.at_budget:
        return None
    return f"a {ticket.block_kind.value} block on '{stage_key}' has its repair turn unspent"


def retried_since(session: Session, plan_id: str, since: datetime | None) -> set[str]:
    """The tickets it already retried since it was turned on."""
    query = select(AutopilotEvent.ticket_id).where(
        AutopilotEvent.initiative_id == plan_id,
        AutopilotEvent.action == AutopilotAction.RETRIED,
    )
    if since is not None:
        query = query.where(AutopilotEvent.created_at >= since)
    return {tid for tid in session.exec(query).all() if tid}
