"""Shared active-run lookups used to keep triage and stage runs mutually exclusive."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from loregarden.models.domain import AgentRun, OrchestrationRun, OrchestrationRunStatus, RunStatus
from sqlmodel import Session, col, select

IN_FLIGHT_STATUSES = [RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]


def new_run_code() -> str:
    return f"run_{secrets.token_hex(3)}"


def find_active_workspace_chat_run(
    session: Session, workspace_id: str, *, stage_key: str
) -> AgentRun | None:
    """In-flight ticket-less run for a workspace-scoped chat channel.

    ``find_active_run`` keys on a ticket, which Home chat does not have; its
    turns still hold a live CLI against the workspace checkout, so they need
    the same one-at-a-time guard.
    """
    return session.exec(
        select(AgentRun).where(
            col(AgentRun.ticket_id).is_(None),
            AgentRun.workspace_id == workspace_id,
            AgentRun.stage_key == stage_key,
            col(AgentRun.status).in_(IN_FLIGHT_STATUSES),
        )
    ).first()


def find_active_run(
    session: Session, ticket_id: str, *, only_agent_id: str | None = None
) -> AgentRun | None:
    """Return the first in-flight AgentRun for a ticket, if any.

    "In-flight" means RUNNING or AWAITING_PERMISSION — both hold a live CLI
    subprocess against the workspace's on-disk checkout, which is not
    worktree-isolated on the default execution path. The exception is a
    terminal-handoff run, which has no supervising process — callers that must
    not block on a phantom should reap provably dead ones first via
    ``run_service.fail_stale_handoff_runs``.
    """
    query = select(AgentRun).where(
        AgentRun.ticket_id == ticket_id,
        col(AgentRun.status).in_(IN_FLIGHT_STATUSES),
    )
    if only_agent_id is not None:
        query = query.where(AgentRun.agent_id == only_agent_id)
    return session.exec(query).first()


def find_active_orchestration_run(session: Session, ticket_id: str) -> OrchestrationRun | None:
    """The orchestration in flight for this ticket, claimed or executing.

    QUEUED counts. A caller claims its run before handing the work to a
    background thread (see `claim_orchestration_run`), and between those two
    moments the ticket is already spoken for — treating only RUNNING as active
    would let a second start slip into that window.

    Lives here rather than on `OrchestrationCallbackService` because it is a
    plain query, and needing it was the only reason lower modules reached up to
    that service — which is one half of the orchestration/callbacks cycle.
    """
    return session.exec(
        select(OrchestrationRun)
        .where(OrchestrationRun.ticket_id == ticket_id)
        .where(
            col(OrchestrationRun.status).in_(
                (OrchestrationRunStatus.QUEUED, OrchestrationRunStatus.RUNNING)
            )
        )
        .order_by(col(OrchestrationRun.created_at).desc())
    ).first()


#: How long an orchestration may go without a single control-plane write before
#: its lane is reclaimable. Renewed by `OrchestrationCallbackService.touch_lease`
#: on every stage start, completion, skip and block, so a session doing real work
#: renews many times over; only one that has stopped talking to us expires.
ORCHESTRATION_LEASE = timedelta(minutes=30)


def orchestration_lease_expired(
    run: OrchestrationRun, *, lease: timedelta = ORCHESTRATION_LEASE
) -> bool:
    """Whether a run has gone quiet for longer than the lease allows.

    A run that has never been renewed falls back to when it started, so a row
    written before the lease existed — or a claim whose driver died before it
    did anything — is reclaimable on the first sweep rather than needing a
    backfill.
    """
    stamp = run.last_seen_at or run.started_at or run.created_at
    if stamp is None:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - stamp > lease
