"""Resume a ticket a capacity wait blocked, once the machine's line has cleared.

A capacity block is the one block whose cause is gone without anyone acting on
it: the holders it named finish and the line empties. Its settlement said so
("this clears when the holders the message names finish"), but nothing watched
for that. The lane re-dispatches a stage whose own run failed, so a run that
waited and gave up was retried, and a criteria check or gate that gave up after
its stage's run *succeeded* was not. lg-milestone-that-1425 sat blocked on
'verify' that way until a person requeued it, and a requeue does not dispatch.

So this resumes it on the reconcile timer, the way `resume_usage_limit_waits`
resumes a stage once a provider's limit resets: the cause that clears by itself
is checked, and the stage is requeued and its drive restarted.

**When the line is clear, not on every pass.** The block happened because
nothing ahead finished for the stall window. Resuming into a line that still
has waiters just starts the same wait again, so a ticket is a violation only
while no lease is waiting for capacity.

**Bounded by the transient-retry budget.** No agent ran, so it is not charged
to the stage's attempts. It is charged as a transient retry, like a stranded
stage, so a stage that cannot get capacity even on a clear line stops after
`max_transient_retries` and stays with a person.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from loregarden.models.domain import (
    BlockKind,
    DockerLease,
    DockerLeaseStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.capacity_blocks import CAPACITY_BLOCK_SIGNATURES, latest_orchestration
from loregarden.services.invariants import Violation
from loregarden.services.orchestration_recovery import (
    InterruptionResume,
    admit_resumes,
    requeue_and_plan_resume,
    schedule_interrupted_resumes,
)
from loregarden.services.run_concurrency import find_active_orchestration_run
from loregarden.services.stage_retry_budget import resolve_stage_retry_budget
from loregarden.services.stage_transient_retry import (
    count_transient_retries,
    record_transient_retry,
)
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Names this retry in the transient-retry ledger.
CAPACITY_RETRY_REASON = "capacity_wait"

RESUME_REASON = (
    "The machine's capacity line has cleared; the stage that could not get its lease "
    "is re-run automatically."
)


@dataclass(frozen=True)
class ClearedCapacityBlock:
    ticket: Ticket
    stage_key: str


def line_is_clear(session: Session) -> bool:
    """Whether no lease is waiting for capacity, in either pool (there is one line)."""
    waiting = session.exec(
        select(DockerLease.id).where(DockerLease.status == DockerLeaseStatus.WAITING).limit(1)
    ).first()
    return waiting is None


def _blocked_by_capacity(session: Session, ticket: Ticket) -> bool:
    said = [ticket.blocking_issues or ""]
    run = latest_orchestration(session, ticket.id)
    if run is not None:
        said.append(run.error_message or "")
    return any(signature in text for text in said for signature in CAPACITY_BLOCK_SIGNATURES)


def _budget_left(session: Session, ticket: Ticket, stage_key: str) -> bool:
    config = resolve_stage_retry_budget(session, ticket)
    return count_transient_retries(session, ticket.id, stage_key) < config.max_transient_retries


def cleared_capacity_blocks(session: Session) -> list[ClearedCapacityBlock]:
    """Every ticket a capacity wait blocked that could run now and nothing is driving."""
    blocked = session.exec(
        select(Ticket).where(
            Ticket.state == TicketState.BLOCKED,
            Ticket.block_kind == BlockKind.HARNESS,
            Ticket.workflow_stage_status == StageStatus.BLOCKED,
        )
    ).all()
    candidates = [
        ticket
        for ticket in blocked
        if not ticket.state_locked
        and ticket.workflow_stage_key
        and _blocked_by_capacity(session, ticket)
        and find_active_orchestration_run(session, ticket.id) is None
        and _budget_left(session, ticket, ticket.workflow_stage_key)
    ]
    # Asked once, and only when something could be resumed.
    if not candidates or not line_is_clear(session):
        return []
    return [ClearedCapacityBlock(ticket, ticket.workflow_stage_key) for ticket in candidates]


def cleared_capacity_violations(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=block.ticket.id,
            ticket_id=block.ticket.id,
            entered_at=block.ticket.updated_at,
            detail=f"blocked on '{block.stage_key}' by a capacity wait; the line is now clear",
        )
        for block in cleared_capacity_blocks(session)
    ]


def resume_cleared_capacity_blocks(session: Session, violations: Sequence[Violation]) -> int:
    """Requeue and restart each still-blocked one, charging a transient retry."""
    wanted = {violation.subject_id for violation in violations}
    plans: list[tuple[Ticket, InterruptionResume]] = []
    for block in cleared_capacity_blocks(session):
        if block.ticket.id not in wanted:
            continue
        ticket, stage_key = block.ticket, block.stage_key
        spent = count_transient_retries(session, ticket.id, stage_key)
        limit = resolve_stage_retry_budget(session, ticket).max_transient_retries
        plan = requeue_and_plan_resume(session, ticket, stage_key, reason=RESUME_REASON)
        if plan is None:
            continue
        record_transient_retry(
            session,
            ticket.id,
            stage_key,
            run_id="",
            reason=CAPACITY_RETRY_REASON,
            message=(
                f"'{stage_key}' could not get machine capacity; the line has cleared, so it is "
                f"re-run automatically (retry {spent + 1} of {limit})."
            ),
        )
        plans.append((ticket, plan))
    requests, handled = admit_resumes(session, plans)
    schedule_interrupted_resumes(requests)
    if handled:
        logger.warning(
            "Capacity line cleared; resumed %d ticket(s) a capacity wait blocked "
            "(%d started now): %s",
            len(handled),
            len(requests),
            ", ".join(handled),
        )
    return len(handled)
