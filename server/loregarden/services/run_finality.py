"""A run's first final status is its verdict; a later completion is refused.

`OrchestrationService.complete_run` used to write whatever status it was handed.
When two things finished one run (the lease sweep and an agent it had given up
on, a cancel and the CLI it killed), the later one won. It rewrote the status,
wiped the stderr that explained the first verdict, and ran the completion tail
again over a workflow the first verdict had already moved. run_f4bf51
(2026-11-03) was failed by the sweep at 22:18 and rewritten `succeeded` at
22:41; 65 runs carried more than one completion when this landed
(lg-run-durability-2093).

**Claimed by compare-and-swap, not read-then-write.** The status column moves
from in flight to final in one UPDATE guarded on the in-flight statuses, and
only the writer whose UPDATE matched carries on. A read first would let two
completers both see RUNNING.

**Refused visibly.** A refused completion is logged at warning and noted on the
run's stderr, which is where an operator reads the run: the agent did report
back, and the row says so without that report replacing the verdict.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from loregarden.models.domain import AgentRun, DomainEvent, EventType, RunStatus
from loregarden.models.domain.enums import utcnow
from loregarden.services.run_lease import IN_FLIGHT
from pydantic import BaseModel
from sqlmodel import Session, col, func, select, update

logger = logging.getLogger(__name__)


class CompletionPayload(BaseModel):
    """`AgentRunCompleted`'s payload, as `run_completion.complete_run_tail` writes it."""

    status: RunStatus


#: Starts the note the repair writes; the detector skips a run that carries it.
OVERWRITE_NOTE = "Completed more than once:"


def claim_completion(session: Session, run_id: str, status: RunStatus) -> bool:
    """Move `run_id` to `status` iff it is still in flight; True when this call won.

    `no_autoflush`, so a caller's own unflushed edit to the run cannot reach the
    database ahead of the guard and make the guard compare against it.
    """
    with session.no_autoflush:
        result = session.exec(
            update(AgentRun)
            .where(col(AgentRun.id) == run_id)
            .where(col(AgentRun.status).in_(IN_FLIGHT))
            .values(status=status)
            .execution_options(synchronize_session=False)
        )
    return bool(result.rowcount == 1)


def refuse_late_completion(session: Session, run: AgentRun, *, status: RunStatus) -> AgentRun:
    """Keep the verdict `run` already has, and record the completion that came late."""
    # Reloads the verdict, and discards whatever the caller set on the instance
    # expecting this completion to carry it.
    session.refresh(run)
    note = (
        f"Late completion refused at {utcnow().isoformat()}: reported {status.value} "
        f"after this run was already {run.status.value}, which stands."
    )
    logger.warning("Run %s: %s", run.run_code, note)
    run.stderr = f"{run.stderr}\n{note}" if run.stderr else note
    session.add(run)
    session.commit()
    return run


def _completion_counts(session: Session) -> list[tuple[str, int]]:
    rows = session.exec(
        select(DomainEvent.run_id, func.count())
        .where(DomainEvent.type == EventType.AGENT_RUN_COMPLETED)
        .where(col(DomainEvent.run_id).is_not(None))
        .group_by(DomainEvent.run_id)
        .having(func.count() > 1)
    ).all()
    return [(run_id, count) for run_id, count in rows if run_id is not None]


def completions(session: Session, run_id: str) -> list[DomainEvent]:
    """`run_id`'s completion events, oldest first."""
    return list(
        session.exec(
            select(DomainEvent)
            .where(DomainEvent.type == EventType.AGENT_RUN_COMPLETED)
            .where(DomainEvent.run_id == run_id)
            .order_by(col(DomainEvent.created_at))
        ).all()
    )


def _completed_status(event: DomainEvent) -> RunStatus:
    return CompletionPayload.model_validate_json(event.payload_json).status


def overwritten_runs(session: Session) -> list[AgentRun]:
    """Runs completed more than once whose row does not yet say so."""
    runs = (session.get(AgentRun, run_id) for run_id, _count in _completion_counts(session))
    return [run for run in runs if run is not None and OVERWRITE_NOTE not in (run.stderr or "")]


def note_overwritten_runs(session: Session, run_ids: Sequence[str]) -> int:
    """Write each run's completion history onto its stderr.

    The status is left as it is: whatever the last completion did to the
    workflow has been acted on since, and reverting the column would make the
    row disagree with the ticket. What was lost is the record that the first
    verdict existed, and that is what this restores.
    """
    noted = 0
    for run_id in run_ids:
        run = session.get(AgentRun, run_id)
        if run is None or OVERWRITE_NOTE in (run.stderr or ""):
            continue
        history = " > ".join(
            f"{_completed_status(event).value} at {event.created_at.isoformat()}"
            for event in completions(session, run.id)
        )
        note = f"{OVERWRITE_NOTE} {history}. The status shown is the last of these."
        run.stderr = f"{run.stderr}\n{note}" if run.stderr else note
        session.add(run)
        noted += 1
    session.commit()
    return noted
