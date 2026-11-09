"""Each agent-run dispatch carries an idempotency key, so a replay starts one run.

`loregarden_start_orchestration` already takes an idempotency key, so an
ambiguous start can be retried safely. A single stage dispatch inside it had
none: a dispatch retried after a crash, a restart or a timeout (a harness
re-sending `begin_external_stage`, a queue lane re-reading its entry, a client
re-posting a start) started a second agent on the same stage.

**The key** is the dispatch's lane and attempt: the orchestration run (or the
ticket, for a dispatch outside one), the stage, the agent and skill (a parallel
stage runs several members side by side), and the attempt, which is one more
than the lane's settled runs. So while a lane's run is in flight, every
dispatch of that lane computes the run's own key; once it settles, the next
dispatch is the next attempt. `agent_runs.dispatch_key` carries a unique index,
so two dispatches racing past the read still make one row.

**A replay reads the run; it never dispatches again.** A dispatch whose key
names a run in flight gets that run back, whatever its lease says. If its
supervisor died, the lease sweep settles it, and only then does the lane move
to its next attempt. And a returned run is executed at most once
(`claim_execution`), so the replay that hands it back cannot start a second
agent on it either.

Prior art: obey's client contract keys every submitted turn with a key the
caller saves first; a retry with the same key returns the original turn, and an
uncertain acceptance is kept and never retried automatically (recorded on
lg-milestone-that-2490's "Prior art — reference survey" document).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from functools import wraps
from typing import Protocol

from loregarden.models.domain import AgentRun, Ticket
from loregarden.models.domain.enums import DispatchSurface
from loregarden.models.domain.orchestrator_decisions import OrchestratorDecision
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.run_lease import IN_FLIGHT
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def dispatch_key(
    session: Session,
    ticket: Ticket,
    *,
    orchestration_run_id: str | None,
    stage_key: str,
    agent_id: str,
    skill_name: str | None,
    lane: str = "",
) -> str:
    """The key of the dispatch this lane would make now.

    `lane` separates dispatches that run side by side on purpose with the same
    agent, such as a fan-out's competing attempts. The attempt counts the lane's
    settled runs by their keys rather than their columns: a run's orchestration
    can be stamped on it after it was dispatched (an external harness's checkout),
    and the key is what that dispatch was.
    """
    scope = f"orch:{orchestration_run_id}" if orchestration_run_id else f"ticket:{ticket.id}"
    prefix = f"{scope}|{stage_key}|{agent_id}|{skill_name or ''}|{lane}|"
    settled = session.exec(
        select(func.count()).where(
            AgentRun.ticket_id == ticket.id,
            func.substr(AgentRun.dispatch_key, 1, len(prefix)) == prefix,
            col(AgentRun.status).not_in(IN_FLIGHT),
        )
    ).one()
    return f"{prefix}{settled + 1}"


def dispatched_run(session: Session, key: str) -> AgentRun | None:
    """The run a dispatch with `key` already made, if any."""
    return session.exec(select(AgentRun).where(AgentRun.dispatch_key == key)).first()


def claim_execution(session: Session, run: AgentRun) -> bool:
    """Claim `run` for execution; False when something already executed it.

    One conditional write, so two executors handed the same run cannot both
    win. Commits.
    """
    claimed = session.exec(
        update(AgentRun)
        .where(col(AgentRun.id) == run.id, col(AgentRun.execution_claimed_at).is_(None))
        .values(execution_claimed_at=datetime.now(timezone.utc))
    )
    session.commit()
    session.refresh(run)
    return bool(claimed.rowcount)


def record_dispatch_replay(
    session: Session, ticket: Ticket, run: AgentRun, *, surface: DispatchSurface
) -> None:
    """Put a dispatch that got an in-flight run back on the ticket's history."""
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.DISPATCH_REPLAYED,
        stage_key=run.stage_key,
        reason=(
            f"A {surface.value} dispatch of {run.stage_key} ({run.agent_id}) repeated one "
            f"whose run {run.run_code} is still {run.status.value}; it got that run back "
            "instead of starting a second agent."
        ),
        run_id=run.id,
        evidence={"dispatch_key": run.dispatch_key, "surface": surface.value},
    )


def replay_of(
    session: Session, ticket: Ticket, key: str, *, surface: DispatchSurface
) -> AgentRun | None:
    """The run a dispatch with `key` already made, recorded as a replay; None if none."""
    replayed = dispatched_run(session, key)
    if replayed is not None:
        record_dispatch_replay(session, ticket, replayed, surface=surface)
    return replayed


def commit_keyed_run(
    session: Session, ticket: Ticket, run: AgentRun, *, surface: DispatchSurface
) -> AgentRun:
    """Commit the new `run`; if another dispatch of its key committed first, theirs.

    The unique index on `dispatch_key`, not the earlier read, is what makes one
    run of a key: a dispatch that raced past the read collides here.
    """
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = dispatched_run(session, run.dispatch_key)
        if winner is None:
            raise
        record_dispatch_replay(session, ticket, winner, surface=surface)
        return winner
    return run


class _Executor(Protocol):
    session: Session


_Execute = Callable[..., AgentRun]


def executed_once(execute: _Execute) -> _Execute:
    """Wrap an executor's `execute` so a run is executed at most once.

    A replayed dispatch hands back the run it repeated; whoever executes it
    second gets the run as it is instead of starting a second agent on it.
    """

    @wraps(execute)
    def wrapper(
        executor: _Executor,
        run: AgentRun,
        ticket: Ticket,
        *,
        advance_workflow: bool = True,
        skip_git_branch: bool = False,
    ) -> AgentRun:
        if not claim_execution(executor.session, run):
            logger.warning(
                "Run %s was already executed; not starting a second agent on it (%s)",
                run.run_code,
                run.status.value,
            )
            return run
        return execute(
            executor,
            run,
            ticket,
            advance_workflow=advance_workflow,
            skip_git_branch=skip_git_branch,
        )

    return wrapper
