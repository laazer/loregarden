"""Persisted per-(ticket, stage) dispatch counter — the circuit breaker that
stops a stage from being redispatched indefinitely (ticket 105).

The count is derived purely from dedicated ``Artifact`` rows, the same
durable-counter pattern as the gate-fix attempt counter (``count_gate_fix_attempts``,
below). It is deliberately *not* derived from ``AgentRun``
timestamps: two dispatches of the same self-redoing stage within one
``execute()`` call can land in the same instant (an agent whose stage report
reroutes to its own stage_key resets it straight back to PENDING), so there is
no per-run time gap to cluster on. Persisting an explicit marker per dispatch
pass is what makes the counter survive a server restart and hold across
separate orchestration runs against the same ticket.

One marker is written per dispatch *pass* — once for a whole parallel stage,
not once per member agent — because the caller invokes
``record_stage_dispatch`` once per turn of the orchestrator's main loop,
regardless of how many agents that turn spawns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loregarden.models.domain import Artifact
from sqlmodel import Session, select

if TYPE_CHECKING:
    from loregarden.models.domain import OrchestrationRun, Ticket
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
    from loregarden.services.orchestration_profile import RetryBudgetConfig

# Dedicated artifact kind so the counter never collides with diff/log/test/
# evidence/error artifacts; the stage key rides in the title, scoping the count
# to (ticket, stage) together.
_DISPATCH_KIND = "stage_dispatch"


def _dispatch_title(stage_key: str) -> str:
    return f"stage-dispatch:{stage_key}"


def record_stage_dispatch(session: Session, ticket_id: str, stage_key: str) -> None:
    """Record one dispatch pass of ``stage_key`` for ``ticket_id``.

    Called by the orchestrator exactly once per dispatch pass, before any agent
    for that pass runs. Committed so the count survives a restart and is visible
    to a fresh session.
    """
    session.add(
        Artifact(
            ticket_id=ticket_id,
            kind=_DISPATCH_KIND,
            title=_dispatch_title(stage_key),
        )
    )
    session.commit()


def count_stage_dispatches(session: Session, ticket_id: str, stage_key: str) -> int:
    """How many times ``record_stage_dispatch`` has run for this (ticket, stage)
    pair, ever — across every orchestration run against the ticket."""
    return len(
        session.exec(
            select(Artifact)
            .where(Artifact.ticket_id == ticket_id)
            .where(Artifact.kind == _DISPATCH_KIND)
            .where(Artifact.title == _dispatch_title(stage_key))
        ).all()
    )


def stage_retry_block_message(stage_key: str, attempts: int, max_attempts: int) -> str:
    """ "" while ``attempts < max_attempts``; a human-readable block message once
    the budget is exhausted.

    Worded to hold whether the exhausted attempts passed or failed: the same
    breaker fires for a stage that kept reporting ``pass`` without the workflow
    advancing past it as for one that kept failing, so it must not accuse the
    stage of repeated failure.
    """
    if attempts < max_attempts:
        return ""
    return (
        f"Stage '{stage_key}' reached its retry budget of {max_attempts} dispatches "
        "without the workflow advancing past it. Blocking for a human rather than "
        "dispatching it again."
    )


def exceeds_stage_retry_budget(
    session: Session,
    ticket_id: str,
    stage_key: str,
    *,
    enabled: bool,
    max_attempts: int,
) -> str:
    """The composed pre-dispatch check the orchestrator calls: "" when the
    budget is disabled or the stage is still within it, else the block
    message."""
    if not enabled:
        return ""
    attempts = count_stage_dispatches(session, ticket_id, stage_key)
    return stage_retry_block_message(stage_key, attempts, max_attempts)


def enforce_stage_retry_budget(
    session: Session,
    callbacks: OrchestrationCallbackService,
    orch_run: OrchestrationRun,
    ticket: Ticket,
    stage_key: str,
    config: RetryBudgetConfig,
) -> OrchestrationRun | None:
    """Pre-dispatch guard for the orchestrator's main loop.

    When ``stage_key`` is at its retry budget: block the ticket and return the
    now-BLOCKED run for the caller to hand straight back. Otherwise record this
    dispatch pass and return ``None`` so the caller proceeds.
    """
    block = exceeds_stage_retry_budget(
        session,
        ticket.id,
        stage_key,
        enabled=config.enabled,
        max_attempts=config.max_attempts_per_stage,
    )
    if block:
        callbacks.block_ticket(orch_run, ticket, stage_key=stage_key, message=block)
        session.refresh(orch_run)
        return orch_run
    record_stage_dispatch(session, ticket.id, stage_key)
    return None


# -- Gate-fix attempt counter -------------------------------------------------
# The sibling durable counter this module's dispatch counter is modelled on: the
# builtin orchestrator's automatic transition-gate fix retries, counted the same
# way (persisted rows, not in-process state) so the budget holds across separate
# orchestration runs. Housed here alongside the dispatch counter rather than in
# the already-oversized orchestrator, since both are the identical pattern.


def gate_failure_artifact_title(stage_key: str) -> str:
    return f"Transition gate failed — {stage_key}"


def count_gate_fix_attempts(session: Session, ticket_id: str, stage_key: str) -> int:
    """Count prior automatic gate-fix retries for this stage, persisted via the
    error artifacts `_reroute_for_agent_fix`/`_block_after_gate_failure` attach —
    so the retry budget holds across separate orchestration runs, not just within
    a single `execute()` call. A function-local counter resets every time a new
    run starts (e.g. an operator or auto-resume re-triggers orchestration after a
    pause), letting a stage that can never pass its gate (a persistent
    environment issue, not something an agent can fix by editing code) cycle
    indefinitely instead of ever durably giving up.
    """
    return len(
        session.exec(
            select(Artifact)
            .where(Artifact.ticket_id == ticket_id)
            .where(Artifact.kind == "error")
            .where(Artifact.title == gate_failure_artifact_title(stage_key))
        ).all()
    )
