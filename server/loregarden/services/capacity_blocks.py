"""Blocks a capacity wait caused, filed as the ticket's work.

A run that never got its lease did no work: the machine was full. Before the fix
that gave capacity failures their own type (`capacity_errors`), the run boundary
blocked them under the generic stage pointer, the text classifier read that as
WORK, and lg-milestone-that-1431 sat blocked on 'test-design' as if its tests
were wrong. The boundary now declares HARNESS. This finds any that still arrive as
WORK, for the invariant registry, and re-files them.

A block is a capacity wait's when the ticket's latest orchestration ended BLOCKED
with a capacity-unavailable message: the current prefix, or the words the
fixed-budget waits used before they were removed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from loregarden.models.domain import (
    BlockKind,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestratorDecision,
    Ticket,
    TicketState,
)
from loregarden.services.capacity_errors import CAPACITY_UNAVAILABLE_PREFIX
from loregarden.services.invariants import Violation
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: What a capacity wait's failure says: today's prefix, and the two messages the
#: fixed-budget waits wrote before 2026-11-06, which rows on the live database
#: still carry.
CAPACITY_BLOCK_SIGNATURES: tuple[str, ...] = (
    CAPACITY_UNAVAILABLE_PREFIX,
    "of host capacity: still queued",
    "docker capacity and did not get it within",
)


@dataclass(frozen=True)
class CapacityWorkBlock:
    ticket: Ticket
    stage_key: str
    blocked_at: datetime | None


def _latest_orchestration(session: Session, ticket_id: str) -> OrchestrationRun | None:
    return session.exec(
        select(OrchestrationRun)
        .where(OrchestrationRun.ticket_id == ticket_id)
        .order_by(col(OrchestrationRun.created_at).desc())
    ).first()


def _is_capacity_failure(run: OrchestrationRun) -> bool:
    message = run.error_message or ""
    return any(signature in message for signature in CAPACITY_BLOCK_SIGNATURES)


def work_blocks_from_capacity_waits(session: Session) -> list[CapacityWorkBlock]:
    """Every ticket blocked as WORK whose latest orchestration ended on a capacity wait."""
    found: list[CapacityWorkBlock] = []
    blocked = session.exec(
        select(Ticket).where(
            Ticket.state == TicketState.BLOCKED, Ticket.block_kind == BlockKind.WORK
        )
    ).all()
    for ticket in blocked:
        run = _latest_orchestration(session, ticket.id)
        if run is None or run.status is not OrchestrationRunStatus.BLOCKED:
            continue
        if not _is_capacity_failure(run):
            continue
        found.append(
            CapacityWorkBlock(
                ticket, run.current_stage_key or ticket.workflow_stage_key, run.finished_at
            )
        )
    return found


def refile_capacity_blocks(session: Session, ticket_ids: Sequence[str]) -> int:
    """Re-file each still-misfiled capacity block as HARNESS, saying so in its history."""
    wanted = set(ticket_ids)
    refiled = 0
    for block in work_blocks_from_capacity_waits(session):
        if block.ticket.id not in wanted:
            continue
        block.ticket.block_kind = BlockKind.HARNESS
        session.add(block.ticket)
        record_orchestrator_decision(
            session,
            block.ticket,
            decision=OrchestratorDecision.CLASSIFIED_BLOCK,
            stage_key=block.stage_key,
            reason=(
                f"Block on '{block.stage_key}' re-filed as harness: its run never got machine "
                "capacity, so no agent looked at the work. Requeue it once the machine is free."
            ),
            evidence={"block_kind": BlockKind.HARNESS.value, "was": BlockKind.WORK.value},
        )
        logger.warning(
            "Re-filed %s's block on '%s' from work to harness: its run never got capacity",
            block.ticket.external_id or block.ticket.id,
            block.stage_key,
        )
        refiled += 1
    session.commit()
    return refiled


def capacity_block_violations(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=block.ticket.id,
            ticket_id=block.ticket.id,
            entered_at=block.blocked_at,
            detail=f"blocked as work on '{block.stage_key}', where its run never got capacity",
        )
        for block in work_blocks_from_capacity_waits(session)
    ]


def refile_capacity_violations(session: Session, violations: Sequence[Violation]) -> int:
    return refile_capacity_blocks(session, [violation.subject_id for violation in violations])
