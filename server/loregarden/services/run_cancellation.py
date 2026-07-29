"""Cooperative cancellation for in-flight agent and orchestration runs.

Runs execute on fire-and-forget daemon threads; there is no process-handle
registry the API can signal. Cancellation is therefore a DB flag — the same
shape as run steering — that the permission bridge, print-mode CLI loop, and
builtin orchestrator poll with their own short-lived sessions so they see the
API's commit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from loregarden.db.session import engine
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    RunStatus,
)
from loregarden.services.orchestration import OrchestrationService
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

_CANCELLABLE_RUN_STATUSES = frozenset(
    {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION}
)
_CANCELLABLE_ORCH_STATUSES = frozenset(
    {OrchestrationRunStatus.QUEUED, OrchestrationRunStatus.RUNNING}
)


def cancel_refusal(run: AgentRun | None) -> str:
    """Why this run cannot be cancelled, or "" when it can be."""
    if run is None:
        return "Run not found."
    if run.status not in _CANCELLABLE_RUN_STATUSES:
        return f"Run is {run.status.value.lower()}, so there is nothing to cancel."
    if run.cancel_requested_at is not None:
        return "Cancel already requested."
    return ""


def orchestration_cancel_refusal(orch_run: OrchestrationRun | None) -> str:
    """Why this orchestration cannot be cancelled, or "" when it can be."""
    if orch_run is None:
        return "Orchestration run not found."
    if orch_run.status not in _CANCELLABLE_ORCH_STATUSES:
        return (
            f"Orchestration is {orch_run.status.value.lower()}, "
            "so there is nothing to cancel."
        )
    if orch_run.cancel_requested_at is not None:
        return "Cancel already requested."
    return ""


def cancel_requested(run_id: str) -> bool:
    """Whether the API has asked this agent run to stop.

    Opens its own short-lived session: the API commits from a different
    connection, and the session driving the run may sit in a transaction old
    enough never to see that write.
    """
    with Session(engine) as session:
        run = session.get(AgentRun, run_id)
        return bool(run and run.cancel_requested_at is not None)


def orchestration_cancel_requested(orch_run_id: str) -> bool:
    """Whether the API has asked this orchestration run to stop.

    Same fresh-session pattern as ``cancel_requested``.
    """
    with Session(engine) as session:
        orch_run = session.get(OrchestrationRun, orch_run_id)
        return bool(orch_run and orch_run.cancel_requested_at is not None)


def request_cancel(session: Session, run: AgentRun) -> AgentRun:
    """Record a cancel request. Raises ValueError when the run cannot take one."""
    refusal = cancel_refusal(run)
    if refusal:
        raise ValueError(refusal)

    run.cancel_requested_at = datetime.now(timezone.utc)
    session.add(run)

    if run.status == RunStatus.QUEUED:
        _drop_queued_run(session, run.id)
        session.commit()
        session.refresh(run)
        # Nothing will poll the flag for a never-started queue row — settle now
        # so the stage returns to PENDING via the CANCELLED completion branch.
        return OrchestrationService(session).complete_run(
            run,
            status=RunStatus.CANCELLED,
            stderr="Cancelled by operator",
        )

    if run.status == RunStatus.AWAITING_PERMISSION:
        _reject_pending_approvals(session, run.id)

    session.commit()
    session.refresh(run)
    return run


def request_orchestration_cancel(session: Session, orch_run: OrchestrationRun) -> OrchestrationRun:
    """Record a cancel request on an orchestration run."""
    refusal = orchestration_cancel_refusal(orch_run)
    if refusal:
        raise ValueError(refusal)

    orch_run.cancel_requested_at = datetime.now(timezone.utc)
    session.add(orch_run)
    session.commit()
    session.refresh(orch_run)
    return orch_run


def _drop_queued_run(session: Session, run_id: str) -> None:
    """Remove a parallel-queue row if this agent run is waiting for a slot."""
    queued = session.exec(select(QueuedRun).where(QueuedRun.run_id == run_id)).first()
    if queued:
        session.delete(queued)
        logger.info("Removed queued run %s from parallel queue on cancel", run_id)


def _reject_pending_approvals(session: Session, run_id: str) -> None:
    """Clear dangling inbox rows so a cancelled AWAITING_PERMISSION run does not
    leave a permission prompt that can never be answered."""
    pending = session.exec(
        select(Approval).where(
            Approval.run_id == run_id,
            Approval.status == ApprovalStatus.PENDING,
        )
    ).all()
    now = datetime.now(timezone.utc)
    for approval in pending:
        approval.status = ApprovalStatus.REJECTED
        approval.resolved_at = now
        approval.resolved_by = "cancellation"
        session.add(approval)
