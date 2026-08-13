"""What a lane starts, kept above the lane that starts it.

`QueueLaneService` used to dispatch its own work, which meant the queue reached
*up* into the orchestrator: `queue_lanes` imported `orchestration_callbacks` and
`run_service`, both of which reach back down to it. Four function-local imports
held that apart — the cycle was never fixed, only deferred, and a deferred cycle
is still one edge away from an import error nobody can read.

The direction that makes sense is one way: a coordinator drives the queue, the
queue does not drive the coordinator. So dispatch lives here, above the lane,
and the lane calls it through the `LaneDispatcher` protocol it defines itself.
Nothing in `queue_lanes` imports this module.

**Why a registry and not a constructor argument.** The obvious injection —
every caller passes a dispatcher — does not work for the one caller that
matters. `orchestration_callbacks` releases a lane when a run finishes, and
releasing drains the lane, so it would have to pass a dispatcher and therefore
import this module; this module imports `run_service`, which imports
`orchestration_callbacks`. The injector is itself inside the cycle. Resolving
the dispatcher at *runtime* rather than at import time is what breaks it.

Importing this module installs it. `main` and `queue_admission` both do, which
covers every process that can start work; a `QueueLaneService` built where no
dispatcher was ever installed logs an error rather than quietly declining to
run anything (see `start_lane_head`).
"""

from __future__ import annotations

import logging

from loregarden.models.domain import (
    AgentRun,
    OrchestrationDriver,
    OrchestrationRun,
    QueuedRun,
    Ticket,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.queue_lanes import set_lane_dispatcher_factory
from loregarden.services.run_service import schedule_agent_run, schedule_orchestration
from sqlmodel import Session

logger = logging.getLogger(__name__)


class LaneDispatch:
    """Starts the work a lane has reached. Satisfies `LaneDispatcher`."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def dispatch_stage(self, ticket: Ticket, entry: QueuedRun) -> AgentRun | None:
        """Start one stage and return the run that now owns the lane.

        The single-stage twin of `dispatch_orchestration`, for entries parked by
        admission control on behalf of the Dashboard or MCP. A lane holding one
        of these is released by `complete_run_tail` (which frees whatever slot
        names the finished run) rather than by `complete_orchestration`.
        """
        try:
            run = OrchestrationService(self.session).start_run(
                ticket,
                stage_key=entry.stage_key or None,
                auto_approve=entry.auto_approve,
                timeout_override_seconds=entry.timeout_seconds,
            )
        except ValueError as exc:
            logger.warning("Lane stage dispatch failed for ticket %s: %s", ticket.id, exc)
            return None

        schedule_agent_run(run.id)
        return run

    def dispatch_orchestration(
        self,
        ticket: Ticket,
        *,
        auto_approve: bool,
        stop_at_stage_key: str | None,
        driver: str = "",
        max_stages: int | None = None,
        timeout_seconds: int | None = None,
    ) -> OrchestrationRun | None:
        """Start the ticket's pipeline and return the run that now owns the lane.

        The run is *claimed* before the work is handed off, not read back after.
        `schedule_orchestration` executes on a background thread, so a lane that
        dispatched and then looked up the ticket's active run raced that thread
        and usually read nothing — it concluded the dispatch had been refused,
        left its entry queued, and the orchestration ran in no lane at all.
        """
        callbacks = OrchestrationCallbackService(self.session)
        active = callbacks.get_active_orchestration_run(ticket.id)
        if active:
            logger.info(
                "Lane dispatch skipped for ticket %s: %s is already orchestrating",
                ticket.id,
                active.run_code,
            )
            return active

        # An unrecognised driver is the caller's error, not grounds to start
        # this ticket on one nobody asked for.
        try:
            chosen_driver = OrchestrationDriver(driver) if driver else None
        except ValueError:
            logger.warning(
                "Lane dispatch failed for ticket %s: unknown driver %r", ticket.id, driver
            )
            return None

        claim = callbacks.claim_orchestration_run(
            ticket,
            driver=chosen_driver,
            auto_approve=auto_approve,
            stop_at_stage_key=stop_at_stage_key or "",
            timeout_override_seconds=timeout_seconds,
        )
        try:
            schedule_orchestration(
                ticket.id,
                auto_approve=auto_approve,
                stop_at_stage_key=stop_at_stage_key or None,
                driver=chosen_driver,
                max_stages=max_stages,
                timeout_seconds=timeout_seconds,
            )
        except ValueError as exc:
            logger.warning("Lane dispatch failed for ticket %s: %s", ticket.id, exc)
            callbacks.abandon_claim(claim, message=str(exc))
            return None

        return claim


set_lane_dispatcher_factory(LaneDispatch)
