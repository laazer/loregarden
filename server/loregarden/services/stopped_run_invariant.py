"""Invariant: a run asked to stop does not stay in flight.

Kept apart from `run_cancellation` (the guard's writers), which `run_capacity`
imports; the invariant machinery reaches the orchestrator, so the guard must not
import it back.

A stop is honoured within seconds by whatever supervises the run: the agent's
cancel poll kills it, and since lg-workflow-integrity-2751 a run still waiting
in the capacity line leaves on its next poll. A run in flight long after its
stop is one nothing is honouring. That was every run stopped in line before the
fix: Pause on lg-code-knowledge-1765 set the flag and the run waited on, showing
RUNNING, for up to the hour-long stall window.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from loregarden.models.domain import AgentRun, InvariantReentry, RunStatus
from loregarden.models.domain.enums import utcnow
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_detached_stop import stop_detached_process
from sqlmodel import Session, col, select

#: How long a stop may go unhonoured before the run counts as stuck. Far past
#: every honouring path: the cancel poll (seconds), the SIGTERM grace
#: (`run_detached_stop.GRACE_SECONDS`), and the line wait's poll (2s).
STOP_HONOURED_WITHIN = timedelta(minutes=10)

_IN_FLIGHT = (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)

STOP_UNHONOURED_MESSAGE = (
    "Cancelled by operator; settled by reconcile because nothing honoured the stop"
)


def _stuck(run: AgentRun | None, cutoff) -> bool:
    return (
        run is not None
        and run.status in _IN_FLIGHT
        and run.cancel_requested_at is not None
        and run.cancel_requested_at < cutoff
    )


def stopped_runs_still_in_flight(session: Session) -> list[Violation]:
    """Runs still in flight `STOP_HONOURED_WITHIN` after their stop was requested.

    Entered at the stop plus that window: until then the run may simply be
    stopping.
    """
    # Stored naive UTC (see `utcnow`), so the bound is too.
    cutoff = (utcnow() - STOP_HONOURED_WITHIN).replace(tzinfo=None)
    rows = session.exec(
        select(AgentRun.id, AgentRun.ticket_id, AgentRun.cancel_requested_at)
        .where(col(AgentRun.status).in_(_IN_FLIGHT))
        .where(col(AgentRun.cancel_requested_at).is_not(None))
        .where(col(AgentRun.cancel_requested_at) < cutoff)
    ).all()
    return [
        Violation(
            subject_id=run_id,
            ticket_id=ticket_id,
            entered_at=requested_at + STOP_HONOURED_WITHIN,
            detail=f"agent run {run_id} is still in flight {STOP_HONOURED_WITHIN} after its stop",
        )
        for run_id, ticket_id, requested_at in rows
    ]


def settle_stopped_runs(session: Session, violations: Sequence[Violation]) -> int:
    """Signal each stuck run's process if it is still ours, then settle it CANCELLED.

    Re-checks each run: it may have finished since detection. The CANCELLED
    completion branch leaves its stage PENDING with no block, as a stop does.
    """
    cutoff = (utcnow() - STOP_HONOURED_WITHIN).replace(tzinfo=None)
    orch = OrchestrationService(session)
    repaired = 0
    for violation in violations:
        run = session.get(AgentRun, violation.subject_id)
        if not _stuck(run, cutoff):
            continue
        # Never raises; refuses a pid it cannot vouch for.
        stop_detached_process(run)
        orch.complete_run(run, status=RunStatus.CANCELLED, stderr=STOP_UNHONOURED_MESSAGE)
        repaired += 1
    return repaired


STOPPED_RUNS_DO_NOT_STAY_IN_FLIGHT = Invariant(
    name="stopped_runs_do_not_stay_in_flight",
    ticket="lg-workflow-integrity-2751",
    statement="an agent run asked to stop is not still in flight ten minutes later",
    detect=stopped_runs_still_in_flight,
    repair=settle_stopped_runs,
    reentry=InvariantReentry.DEFECT,
    # The day after the guard was written: the live server keeps old waiters
    # until it restarts on the merged build.
    since=date(2026, 11, 20),
    prevented_by=(
        "server/tests/test_stop_while_waiting_for_capacity.py"
        "::test_pause_while_waiting_in_line_cancels_without_starting_the_agent"
    ),
)
