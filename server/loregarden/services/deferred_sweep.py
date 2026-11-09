"""Retry a plan's deferred tickets once other work settles, in sweeps that stop when nothing progresses.

A deferred ticket (`ticket_deferral`) stays blocked while its dependents go
ahead. Nothing re-attempted it after they landed, though the world it was
blocked on had changed. fest hands deferred blockers back once other work
settles, in sweeps: a new sweep starts only if the previous one completed
something, and a hard bound ends them with what remains. That is adopted here.

When an autopilot tick queues nothing and nothing of the plan is in flight, it
calls `sweep_deferred`:

1. A sweep in progress (a ticket it retried still holds a lane) is waited on.
2. A finished sweep that moved none of its tickets to done ends the sweeps.
3. `MAX_DEFERRED_SWEEPS` sweeps end them too.
4. Otherwise the next sweep starts: `SWEEP_STARTED`, then each deferred ticket
   is un-blocked and queued (`SWEEP_RETRIED`), with "deferred sweep N" as why.

Ending records `SWEEPS_ENDED` once, naming the deferred tickets that remain;
the plan sweeps again only after it is turned off and on. Every step is an
`AutopilotEvent`, so "why was this retried?" and "why did it stop?" are on the
plan's log. A retry's brief says what landed since the deferral
(`ticket_deferral.deferral_context_lines`).
"""

from __future__ import annotations

from collections.abc import Callable

from loregarden.models.domain import (
    AutopilotAction,
    AutopilotEvent,
    InitiativePlan,
    Ticket,
    TicketState,
)
from loregarden.services.deferral_state import is_deferred
from loregarden.services.initiative_graph import PlanContext, PlanNode
from sqlmodel import Session, col, select

#: Sweeps per switch-on before it stops with what remains.
MAX_DEFERRED_SWEEPS = 3

#: Queue one deferred ticket for a retry; True when it was queued.
Dispatch = Callable[[PlanNode, str], bool]


def _events(
    session: Session, plan: InitiativePlan, action: AutopilotAction
) -> list[AutopilotEvent]:
    query = select(AutopilotEvent).where(
        AutopilotEvent.initiative_id == plan.initiative_id, AutopilotEvent.action == action
    )
    if plan.autopilot_since is not None:
        query = query.where(col(AutopilotEvent.created_at) >= plan.autopilot_since)
    return list(session.exec(query.order_by(col(AutopilotEvent.created_at))).all())


def _record(session: Session, plan: InitiativePlan, action: AutopilotAction, detail: str) -> None:
    session.add(AutopilotEvent(initiative_id=plan.initiative_id, action=action, detail=detail))
    session.commit()


def _named(nodes: list[PlanNode]) -> str:
    return ", ".join(node.ticket.external_id for node in nodes)


def _swept_in(session: Session, plan: InitiativePlan, start: AutopilotEvent) -> set[str]:
    return {
        event.ticket_id
        for event in _events(session, plan, AutopilotAction.SWEEP_RETRIED)
        if event.ticket_id and event.created_at >= start.created_at
    }


def _end(session: Session, plan: InitiativePlan, deferred: list[PlanNode], why: str) -> None:
    _record(
        session,
        plan,
        AutopilotAction.SWEEPS_ENDED,
        f"{why}; {len(deferred)} deferred ticket(s) remain for a person: {_named(deferred)}",
    )


def sweep_deferred(
    session: Session, plan: InitiativePlan, ctx: PlanContext, dispatch: Dispatch
) -> int:
    """Start the next sweep of deferred tickets when one is due. Returns how many it queued."""
    own = [n for n in ctx.graph.nodes.values() if not n.external and n.id != ctx.initiative.id]
    deferred = [n for n in own if is_deferred(n.ticket)]
    if not deferred or any(n.holds_lane for n in own):
        return 0
    if _events(session, plan, AutopilotAction.SWEEPS_ENDED):
        return 0
    starts = _events(session, plan, AutopilotAction.SWEEP_STARTED)
    if starts:
        swept = _swept_in(session, plan, starts[-1])
        done = {
            ticket.id
            for ticket in session.exec(select(Ticket).where(col(Ticket.id).in_(swept))).all()
            if ticket.state is TicketState.DONE
        }
        if not done:
            _end(
                session,
                plan,
                deferred,
                f"sweep {len(starts)} moved none of the {len(swept)} ticket(s) it retried to done",
            )
            return 0
    if len(starts) >= MAX_DEFERRED_SWEEPS:
        _end(session, plan, deferred, f"{MAX_DEFERRED_SWEEPS} sweeps have run, the bound")
        return 0
    number = len(starts) + 1
    _record(
        session,
        plan,
        AutopilotAction.SWEEP_STARTED,
        f"sweep {number} of at most {MAX_DEFERRED_SWEEPS}: nothing else can start, so it "
        f"retries {len(deferred)} deferred ticket(s): {_named(deferred)}",
    )
    return sum(int(dispatch(node, f"deferred sweep {number}")) for node in deferred)
