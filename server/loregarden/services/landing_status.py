"""What a ticket at its terminal stage is waiting on before it is marked done.

The ticket page said "Landing…" and nothing else for as long as a landing took,
and on 2026-11-06 that was hours: lg-milestone-that-2250's land verification
ran the full server suite, timed out, was refused, and went round again seven
times, while the page showed the same word throughout. Every fact needed to say
more was already recorded:

- the land verification's capacity lease (`LAND_VERIFY_TRANSITION`) says
  whether it is in line, behind whom, or holding and running, since when, and
  — because the commands report onto it — which check and how far through;
- the land verification receipts say how many attempts were refused before
  this one, and why the last one was.

So this reads those, and writes nothing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from loregarden.core.workflow_terminal import find_terminal_stage
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    DockerLease,
    DockerLeaseStatus,
    GateOutcome,
    LandingPhase,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.capacity_line import leases_ahead
from loregarden.services.capacity_progress import describe_progress
from loregarden.services.docker_ledger import OCCUPYING, as_utc
from loregarden.services.land_verification import (
    LAND_VERIFY_TIMEOUT_SECONDS,
    LAND_VERIFY_TRANSITION,
    RECEIPT_FAILED,
)
from loregarden.services.target_branch import target_branch_name
from loregarden.services.workflow_service import resolve_ticket_stages
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: How many of the leases ahead in line are named. The line can be long; the
#: operator needs to see who is first, and how many more there are.
AHEAD_SHOWN = 5


class LeaseAhead(BaseModel):
    """One piece of work the land verification is waiting behind."""

    label: str
    held: bool
    #: When it took capacity (held) or joined the line (waiting).
    since: datetime | None
    #: "pytest on 49 file(s) · 412/1830 (22%)", or "" when it reports nothing.
    progress: str


class RefusedAttempt(BaseModel):
    """The most recent land verification that refused this ticket's landing."""

    at: datetime | None
    #: FAILED (the tests failed) or UNAVAILABLE (they never ran: a timeout, no
    #: capacity, a missing toolchain). They route to different people.
    outcome: GateOutcome
    command: str
    #: None when nothing exited — a timeout or a command that never started.
    exit_code: int | None
    #: The receipt, for the Outputs tab.
    artifact_id: str


class TicketLandingStatus(BaseModel):
    """What stands between this ticket and `done`, read from the ledger."""

    phase: LandingPhase
    #: The branch it lands on.
    target: str
    #: When the current phase began, when the ledger knows.
    since: datetime | None = None
    #: The limit the running tests are held to (VERIFYING only).
    timeout_seconds: int | None = None
    #: Which check, and how far through it (VERIFYING only).
    progress: str = ""
    progress_done: int | None = None
    progress_total: int | None = None
    #: Who the verification waits behind (WAITING_FOR_CAPACITY only).
    ahead: list[LeaseAhead] = []
    ahead_count: int = 0
    #: Land verifications refused since the last one that passed.
    refused_attempts: int = 0
    last_refusal: RefusedAttempt | None = None


class _Receipt(BaseModel):
    """The fields of a land verification receipt this reads."""

    model_config = ConfigDict(extra="ignore")

    status: str
    outcome: str = ""
    command: str = ""
    exit_code: int | None = None


def ticket_landing_status(
    session: Session, ticket: Ticket, workspace: Workspace
) -> TicketLandingStatus:
    """The ticket's landing as the ledger records it now."""
    target = target_branch_name(session, ticket, workspace)
    refused, last = _refusals(session, ticket)
    base = TicketLandingStatus(
        phase=LandingPhase.NOT_LANDING,
        target=target,
        refused_attempts=refused,
        last_refusal=last,
    )

    lease = _land_lease(session, ticket)
    if lease is not None and lease.status is DockerLeaseStatus.WAITING:
        return base.model_copy(update=_waiting(session, lease))
    if lease is not None:
        return base.model_copy(
            update={
                "phase": LandingPhase.VERIFYING,
                "since": as_utc(lease.granted_at),
                "timeout_seconds": LAND_VERIFY_TIMEOUT_SECONDS,
                "progress": describe_progress(
                    lease.progress_step, lease.progress_done, lease.progress_total
                ),
                "progress_done": lease.progress_done,
                "progress_total": lease.progress_total,
            }
        )
    if _landing_run(session, ticket) is not None:
        # Merging, publishing and recording take seconds and leave no row that
        # says when they began, so there is no honest `since` to give.
        return base.model_copy(update={"phase": LandingPhase.MERGING})
    return base


def _land_lease(session: Session, ticket: Ticket) -> DockerLease | None:
    """The ticket's live land verification lease, if it has one.

    The gate lease's label is `gates <transition> · <workspace> · <ticket>`
    (`acquire_gate_capacity`), and the transition is the one `_holding` names.
    """
    return session.exec(
        select(DockerLease)
        .where(
            DockerLease.ticket_id == ticket.id,
            col(DockerLease.status).in_((DockerLeaseStatus.WAITING, *OCCUPYING)),
            col(DockerLease.holder_label).startswith(f"gates {LAND_VERIFY_TRANSITION} "),
        )
        .order_by(col(DockerLease.requested_at).desc())
    ).first()


def _waiting(session: Session, lease: DockerLease) -> dict[str, object]:
    ahead = sorted(
        leases_ahead(session, lease).values(),
        # Holders first, longest-held first; then the line in order.
        key=lambda row: (row.status not in OCCUPYING, row.position, row.requested_at),
    )
    return {
        "phase": LandingPhase.WAITING_FOR_CAPACITY,
        "since": as_utc(lease.requested_at),
        "ahead": [
            LeaseAhead(
                label=row.holder_label,
                held=row.status in OCCUPYING,
                since=as_utc(row.granted_at if row.status in OCCUPYING else row.requested_at),
                progress=describe_progress(
                    row.progress_step, row.progress_done, row.progress_total
                ),
            )
            for row in ahead[:AHEAD_SHOWN]
        ],
        "ahead_count": len(ahead),
    }


def _landing_run(session: Session, ticket: Ticket) -> OrchestrationRun | None:
    """The ticket's running orchestration, when it is on the terminal stage."""
    run = session.exec(
        select(OrchestrationRun)
        .where(
            OrchestrationRun.ticket_id == ticket.id,
            OrchestrationRun.status == OrchestrationRunStatus.RUNNING,
        )
        .order_by(col(OrchestrationRun.created_at).desc())
    ).first()
    if run is None or run.current_stage_key != ticket.workflow_stage_key:
        return None
    _, stages = resolve_ticket_stages(session, ticket)
    terminal = find_terminal_stage(stages or [])
    return run if terminal is not None and terminal.key == run.current_stage_key else None


def _refusals(session: Session, ticket: Ticket) -> tuple[int, RefusedAttempt | None]:
    """How many land verifications were refused since the last that was not,
    and the latest of them."""
    receipts = session.exec(
        select(Artifact)
        .where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == ArtifactKind.LAND_VERIFICATION,
        )
        .order_by(col(Artifact.created_at).desc())
    ).all()
    refused = 0
    last: RefusedAttempt | None = None
    for row in receipts:
        try:
            receipt = _Receipt.model_validate(json.loads(row.content_json))
            if receipt.status != RECEIPT_FAILED:
                break
            attempt = RefusedAttempt(
                at=as_utc(row.created_at),
                outcome=GateOutcome(receipt.outcome),
                command=receipt.command,
                exit_code=receipt.exit_code,
                artifact_id=row.id,
            )
        except (ValueError, ValidationError):
            # A receipt this cannot read is one it cannot count either way;
            # stopping here undercounts rather than inventing a streak.
            logger.warning("landing status: unreadable land verification receipt %s", row.id)
            break
        refused += 1
        last = last or attempt
    return refused, last
