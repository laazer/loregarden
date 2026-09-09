"""Whether a block is final, and how long a lane may be held while it is not.

A lane entry used to end at its first block. The orchestration finished
BLOCKED, `on_orchestration_complete` released the slot, the next entry started —
and any repair that followed (a reload resume, an unconsumed scope-denial pin, a
stage with dispatch budget still on it) had to re-enter admission and wait
behind whatever had taken the lane. The ticket lost its place for a block that
was never final.

So a block that has a *mechanism* behind it holds the lane instead, in
`QueuePosition.REPAIRING`, and settles only when the repair is spent. Two rules
keep that honest:

**A route is a claim that the next dispatch differs.** `RepairRoute` names the
three mechanisms that make one different, and nothing else qualifies. Three
blocks are deliberately excluded: the retry breaker's own, which exists to stop
exactly this retry; a ticket with a pending approval, because a lane held for
work only a person can do is capacity spent on waiting; and a stage whose agent
*declared* a blocker rather than failing — that run answered the question, and
asking it again costs a whole agent turn to hear the same answer.

**The hold is bounded twice.** `REPAIR_ATTEMPT_CAP` bounds how many
re-dispatches one lane occupancy may spend, and `REPAIR_WALL_CLOCK` bounds how
long a single hold may sit before the lane is given back. Whichever is reached
first settles the entry as blocked, exactly as it would have settled at once
before — late, and with a warning naming which cap fired, rather than never.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalStatus,
    QueuedRun,
    QueuePosition,
    RepairRoute,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.run_interruption import blocked_by_interruption
from loregarden.services.stage_retry_budget import (
    blocked_on_stage_retry_budget,
    resolve_stage_retry_budget,
    stage_retry_budget_state,
)
from sqlmodel import Session, col, select

#: How many re-dispatches one lane entry may spend on repairs. Deliberately
#: smaller than the stage retry budget (5 dispatches) that bounds the stage
#: itself: this bounds how many *blocks* a single lane occupancy may absorb, and
#: a ticket that has blocked three times has told us something the fourth
#: attempt will not.
REPAIR_ATTEMPT_CAP = 3

#: How long one hold may last before the lane is handed back. The repair driver
#: runs on the reconciliation timer, so a hold normally lasts one sweep; this is
#: the bound on a hold whose re-dispatch keeps being refused, which would
#: otherwise occupy a lane on a schedule nothing ends.
REPAIR_WALL_CLOCK = timedelta(minutes=15)


def resolve_repair_route(session: Session, ticket: Ticket) -> RepairRoute | None:
    """The mechanism that would make this ticket's next dispatch different, or
    None because its block is final.

    Order matters only in what it reports: a ticket can satisfy more than one,
    and the first is the most specific answer about why the last run stopped.
    """
    if ticket.state in (TicketState.DONE, TicketState.WONT_DO, TicketState.PARKED):
        return None
    # An operator who set the state by hand has decided; repairing past that
    # would overwrite the decision with a retry.
    if ticket.state_locked:
        return None
    if _awaiting_a_person(session, ticket):
        return None

    if blocked_by_interruption(ticket):
        return RepairRoute.INTERRUPTION
    if ticket.scope_reroute_agent:
        return RepairRoute.SCOPE_REROUTE

    stage_key = ticket.workflow_stage_key
    if not stage_key or ticket.workflow_stage_status != StageStatus.BLOCKED:
        return None
    # The breaker's own block is the one thing here that must never be repaired:
    # it fired precisely to stop the stage being dispatched again.
    if blocked_on_stage_retry_budget(session, ticket, stage_key):
        return None
    if not _stage_run_failed(session, ticket, stage_key):
        return None
    budget = stage_retry_budget_state(
        session, ticket, stage_key, resolve_stage_retry_budget(session, ticket)
    )
    if budget.at_budget:
        return None
    return RepairRoute.STAGE_RETRY


def _stage_run_failed(session: Session, ticket: Ticket, stage_key: str) -> bool:
    """Whether this stage's own last run *failed*, rather than declared a blocker.

    The discriminator between a block worth retrying and one that is a
    statement. An agent that calls `loregarden_block_ticket` because the work
    genuinely cannot proceed exits normally and leaves a SUCCEEDED run — running
    it again asks the same question and gets the same answer, at the price of a
    whole agent turn. A run that FAILED did not get to answer.
    """
    run = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id)
        .where(AgentRun.stage_key == stage_key)
        .order_by(col(AgentRun.created_at).desc())
    ).first()
    return run is not None and run.status is RunStatus.FAILED


def _awaiting_a_person(session: Session, ticket: Ticket) -> bool:
    """Whether anything on this ticket is sitting in the approval inbox.

    `block_ticket` files a `record_human_action` approval for a block that names
    human work, so a pending row is the system's own statement that the next
    move is a person's. Holding a lane for that is capacity spent on waiting.
    """
    return (
        session.exec(
            select(Approval.id)
            .where(Approval.ticket_id == ticket.id)
            .where(Approval.status == ApprovalStatus.PENDING)
            .limit(1)
        ).first()
        is not None
    )


def repair_exhausted(entry: QueuedRun, *, now: datetime | None = None) -> str:
    """Why this entry may hold its lane no longer, or "" while it still may.

    A sentence rather than a bool: it is written onto the entry and logged, and
    "the hold ended" without which cap ended it is the kind of report that sends
    the next reader back to the code to find out.
    """
    if entry.repair_attempts >= REPAIR_ATTEMPT_CAP:
        return (
            f"Repair gave up after {entry.repair_attempts} attempts "
            f"(cap {REPAIR_ATTEMPT_CAP}); the block stands."
        )
    held_for = _held_for(entry, now=now)
    if held_for is not None and held_for > REPAIR_WALL_CLOCK:
        return (
            f"Repair gave up after holding the lane for {int(held_for.total_seconds())}s "
            f"(cap {int(REPAIR_WALL_CLOCK.total_seconds())}s); the block stands."
        )
    return ""


def _held_for(entry: QueuedRun, *, now: datetime | None) -> timedelta | None:
    """How long the current hold has lasted, or None because there is none."""
    since = entry.repairing_since
    if since is None:
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - since


def begin_repair(entry: QueuedRun, route: RepairRoute, *, now: datetime | None = None) -> None:
    """Put an entry into a hold. The caller keeps the slot it already claimed."""
    entry.status = QueuePosition.REPAIRING
    entry.repair_route = route
    entry.repairing_since = now or datetime.now(timezone.utc)


def end_repair(entry: QueuedRun) -> None:
    """Close the current hold, leaving `repair_attempts` alone.

    The count is what bounds the whole occupancy, so it survives a repair that
    worked — three successful repairs and a fourth block is still a ticket that
    has spent its lane on blocking.
    """
    entry.repairing_since = None
