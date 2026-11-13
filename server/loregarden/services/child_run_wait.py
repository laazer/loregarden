"""Wait out a child's orchestration that another dispatcher started.

A parent's child pass (`builtin_orchestrator._orchestrate_incomplete_children`)
starts each incomplete child itself. Another dispatcher can get there first: on
2026-11-07 sibling lg-milestone-that-2232 landed, the prerequisite release
resumed lg-milestone-that-2215 at 04:22:35, and two seconds later the parent
lg-milestone-that-1216's pass reached 2215 and was refused with "Orchestration
already running". The refusal reached the parent's run boundary, which blocked
1216 *for a person*. 2215's run then finished its work at 08:55, and nothing
moved the parent again.

The child's work was never in doubt; the parent owed nothing but patience. So
the pass waits for that run to settle and judges the child as if it had driven
it, which is what it would have seen had it won the race.
"""

from __future__ import annotations

import time

from loregarden.models.domain import OrchestrationRun
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.run_cancellation import orchestration_cancel_requested
from loregarden.services.run_concurrency import (
    ACTIVE_ORCHESTRATION_STATUSES,
    OrchestrationAlreadyRunning,
)
from sqlmodel import Session, select

#: How often a waiting parent re-reads its child's run. A child run lasts
#: minutes to hours; a 15s lag in noticing it finished costs nothing.
CHILD_RUN_POLL_SECONDS = 15.0

#: How often the waiting parent renews its own lease. Reading the child every
#: poll is cheap; renewing was a write lock every 15s against a 30-minute lease.
PARENT_RENEW_SECONDS = 120.0


def await_child_run(
    session: Session,
    parent_run: OrchestrationRun,
    running: OrchestrationAlreadyRunning,
    *,
    poll_seconds: float = CHILD_RUN_POLL_SECONDS,
) -> OrchestrationRun | None:
    """Block until the child run ``running`` names settles, and return it.

    Returns None when the parent is cancelled first: a person stopping the
    parent must not wait hours for a child they did not start. The wait renews
    the parent's lease every `PARENT_RENEW_SECONDS`, because this thread is alive
    and doing its job.
    """
    callbacks = OrchestrationCallbackService(session)
    renewed_at: float | None = None
    while True:
        child_run = session.exec(
            select(OrchestrationRun).where(OrchestrationRun.run_code == running.run_code)
        ).one()
        session.refresh(child_run)
        if child_run.status not in ACTIVE_ORCHESTRATION_STATUSES:
            return child_run
        if orchestration_cancel_requested(parent_run.id):
            return None
        if renewed_at is None or time.monotonic() - renewed_at >= PARENT_RENEW_SECONDS:
            renewed_at = time.monotonic()
            callbacks.touch_lease(parent_run)
        session.commit()
        time.sleep(poll_seconds)
