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
#: on every stage start, completion, skip and block — reached by the MCP callbacks,
#: the external harness, and `BuiltinOrchestrator._renew_lease` — so a session doing
#: real work renews many times over; only one that has stopped talking to us expires.
ORCHESTRATION_LEASE = timedelta(minutes=30)


def orchestration_lease_expired(
    session: Session, run: OrchestrationRun, *, lease: timedelta = ORCHESTRATION_LEASE
) -> bool:
    """Whether a run has gone quiet for longer than the lease allows.

    An agent run in flight beneath this orchestration is work in flight, and
    outranks the lease outright. The lease is only renewed at stage boundaries —
    start, completion, skip, block — so a single stage that runs longer than the
    lease has no renewal to give. Two agent runs on the day this was written took
    51 and 38 minutes against a 30-minute lease, so judging on the lease alone
    would have freed a lane out from under an agent that was working, and the
    pool would then have admitted past its own limit.

    That is not the activity-inference this lease exists to replace. For a run
    this server drives, a live agent run *is* its liveness; the lease is what
    covers the case with no agent runs at all — an external harness in someone
    else's terminal, which renews by talking to the control plane.

    A run that has never been renewed falls back to when it started, so a row
    written before the lease existed — or a claim whose driver died before it
    did anything — is reclaimable on the first sweep rather than needing a
    backfill.
    """
    live_agent_run = session.exec(
        select(AgentRun.id)
        .where(AgentRun.orchestration_run_id == run.id)
        .where(col(AgentRun.status).in_(IN_FLIGHT_STATUSES + [RunStatus.QUEUED]))
    ).first()
    if live_agent_run:
        return False

    stamp = _latest_activity(session, run)
    if stamp is None:
        return False
    return datetime.now(timezone.utc) - stamp > lease


def _latest_activity(session: Session, run: OrchestrationRun) -> datetime | None:
    """The most recent moment this orchestration is known to have been alive.

    The lease stamp alone answers for the driver's own writes. A child agent
    run that finished seconds ago answers for the handoff *between* stages: the
    in-flight veto above lapses the instant the last agent of a stage exits, and
    the gate evaluation and next dispatch that follow take seconds during which
    nothing holds the run up. A sweep landing in that window kills a healthy
    orchestration mid-stride — which is how orch_749069 died 345ms after its
    last reviewer passed, with every stage green. A just-finished child is
    activity, so it counts as one.
    """
    latest_child_finish = session.exec(
        select(AgentRun.finished_at)
        .where(AgentRun.orchestration_run_id == run.id)
        .where(col(AgentRun.finished_at).is_not(None))
        .order_by(col(AgentRun.finished_at).desc())
        .limit(1)
    ).first()
    candidates = [
        _as_utc(run.last_seen_at or run.started_at or run.created_at),
        _as_utc(latest_child_finish),
    ]
    known = [stamp for stamp in candidates if stamp is not None]
    return max(known) if known else None


def _as_utc(stamp: datetime | None) -> datetime | None:
    """Naive timestamps are stored as UTC; compare them as such."""
    if stamp is None:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)
