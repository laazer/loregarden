"""Refuse to start a stage under an orchestration that is already over.

A stage was once dispatched 25 seconds after its orchestration had been reaped
for an expired lease: nothing looked, the run started, and a sweeper failed it 13
seconds later with a message about plumbing, leaving the ticket blocked as though
the stage itself had failed (lg-workflow-integrity-688). Refusing costs nothing —
the work could not have been recorded against a terminal parent anyway.

Its own module because `OrchestrationService` sits at the size cap, and because
this is one decision with one reason, which is the shape a person questioning an
unattended run wants to find in one place.
"""

from __future__ import annotations

from loregarden.models.domain import OrchestrationRun, OrchestratorDecision, Ticket
from loregarden.services.interruption_messages import DISPATCH_REFUSED_TERMINAL_PARENT_PREFIX
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from sqlmodel import Session


def refuse_dispatch_under_terminal_parent(
    session: Session,
    ticket: Ticket,
    *,
    stage_key: str | None,
    orchestration_run_id: str | None,
    live_statuses: tuple,
) -> OrchestrationRun | None:
    """The parent orchestration if it is still claiming a lane, else raise.

    Returns None when no parent was named — a standalone dispatch, which the
    manual "Run stage" path depends on and which this guard must leave alone.

    The refusal is recorded where a person can see it, not only where the
    caller catches it: a refused dispatch is the orchestrator choosing not to
    spend a run, and that choice belongs on the ticket's history beside the
    runs it did spend (lg-workflow-integrity-734). Committed before the raise,
    or the rollback that follows takes the record with it.
    """
    if not orchestration_run_id:
        return None
    parent = session.get(OrchestrationRun, orchestration_run_id)
    if parent is None or parent.status in live_statuses:
        return parent
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.REFUSED_DISPATCH_TERMINAL_PARENT,
        stage_key=stage_key or "",
        reason=(
            f"Did not dispatch '{stage_key}': its orchestration "
            f"{parent.run_code} is already {parent.status.value}."
        ),
        evidence={"parent_run_code": parent.run_code, "parent_status": parent.status.value},
    )
    session.commit()
    raise ValueError(
        f"{DISPATCH_REFUSED_TERMINAL_PARENT_PREFIX} {parent.run_code} is {parent.status.value}"
    )
