"""Runs that opened, did nothing until their lease expired, and may not be opened forever.

Every retry cap charges a dispatch: a stage run, a lane's repair, a rework
reroute. A run that dispatches nothing is charged to none of them, so whatever
opened it (initiative autopilot, a lane) saw a ready ticket the moment its lease
expired and opened the next. lmkr-execution-leaves-11 went through 14 in one
day on 2026-11-20, half an hour each, with the board reading "Running".

Two in a row hold the ticket for a person. One is not evidence: the process
driving it can die.
"""

from __future__ import annotations

from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    TicketState,
)
from loregarden.services.interruption_messages import ORCHESTRATION_LEASE_EXPIRED_PREFIX
from loregarden.services.run_concurrency import subtree_orchestration_ids
from sqlmodel import Session, col, select

#: Idle runs in a row on one ticket before it is held.
IDLE_RUN_LIMIT = 2

#: States a ticket can be opened again from. A blocked, parked or finished one is
#: already stopped, for a reason the hold would overwrite.
OPENABLE_STATES = (TicketState.BACKLOG, TicketState.IN_PROGRESS)


def did_nothing(session: Session, run: OrchestrationRun) -> bool:
    """Whether nothing renewed this run and no agent run started under it.

    Both halves are needed. A terminal agent may move stages without starting an
    agent run, and each move renews the lease; a parent's own run renews nothing
    while its children's agents work.
    """
    if run.last_seen_at is not None:
        return False
    beneath = [run.id, *subtree_orchestration_ids(session, run)]
    return (
        session.exec(
            select(AgentRun.id)
            .where(col(AgentRun.orchestration_run_id).in_(beneath))
            .where(AgentRun.created_at >= run.created_at)
            .limit(1)
        ).first()
        is None
    )


def _expired_idle(session: Session, run: OrchestrationRun) -> bool:
    return (
        run.status is OrchestrationRunStatus.FAILED
        and run.error_message.startswith(ORCHESTRATION_LEASE_EXPIRED_PREFIX)
        and did_nothing(session, run)
    )


def idle_streak(session: Session, ticket_id: str) -> list[OrchestrationRun]:
    """The ticket's latest runs that expired having done nothing, newest first.

    Ends at the first run that was anything else, a live one included, and at
    `IDLE_RUN_LIMIT`.
    """
    latest = session.exec(
        select(OrchestrationRun)
        .where(OrchestrationRun.ticket_id == ticket_id)
        .order_by(col(OrchestrationRun.created_at).desc())
        .limit(IDLE_RUN_LIMIT)
    ).all()
    streak: list[OrchestrationRun] = []
    for run in latest:
        if not _expired_idle(session, run):
            break
        streak.append(run)
    return streak


def ends_the_limit(session: Session, run: OrchestrationRun) -> bool:
    """Whether `run`, expiring now, is the last idle run its ticket is allowed."""
    if not did_nothing(session, run):
        return False
    earlier = session.exec(
        select(OrchestrationRun)
        .where(OrchestrationRun.ticket_id == run.ticket_id)
        .where(OrchestrationRun.id != run.id)
        .where(OrchestrationRun.created_at <= run.created_at)
        .order_by(col(OrchestrationRun.created_at).desc())
        .limit(IDLE_RUN_LIMIT - 1)
    ).all()
    return len(earlier) == IDLE_RUN_LIMIT - 1 and all(
        _expired_idle(session, other) for other in earlier
    )


def idle_hold_message(run: OrchestrationRun) -> str:
    return (
        f"{IDLE_RUN_LIMIT} orchestration runs in a row ended having started no agent "
        f"(the last is {run.run_code}, driver {run.driver.value}). Nothing was running it, "
        "so it is held instead of being opened again. Start it on a driver that runs agents, "
        "or attach the terminal agent it was waiting for, then requeue it."
    )
