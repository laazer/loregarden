"""Blocks a gate that never ran caused, filed as the ticket's work.

A gate that came back UNAVAILABLE graded nothing: its cause is the machine. Before
lg-machine-resource-2112 recovery blocked it under the generic "Transition gate
failed" pointer, the text classifier read that as WORK, and the operator went
looking for a defect in code nobody had examined. `gate_recovery` now declares
these HARNESS. This finds any that still arrive as WORK, for the invariant
registry.

A block is that gate's when, on the ticket's latest gate evaluation, the gate was
UNAVAILABLE on the stage the ticket is blocked at, the block was classified after
it, and no agent run started in between. A run in between would be a later stage
attempt, whose block is its own.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from loregarden.models.domain import (
    AgentRun,
    BlockKind,
    DomainEvent,
    EventType,
    GateOutcome,
    OrchestratorDecision,
    Ticket,
    TicketState,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from pydantic import BaseModel
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


class _GatePayload(BaseModel):
    outcome: GateOutcome
    stage_key: str = ""


class _DecisionPayload(BaseModel):
    decision: str


@dataclass(frozen=True)
class MisfiledBlock:
    ticket: Ticket
    stage_key: str
    classified_at: datetime


def _latest(session: Session, ticket_id: str, kind: EventType) -> list[DomainEvent]:
    return list(
        session.exec(
            select(DomainEvent)
            .where(DomainEvent.ticket_id == ticket_id, DomainEvent.type == kind)
            .order_by(col(DomainEvent.created_at).desc())
        ).all()
    )


def _classified_after(session: Session, ticket_id: str, since: datetime) -> datetime | None:
    for event in _latest(session, ticket_id, EventType.ORCHESTRATOR_DECISION):
        if event.created_at < since:
            return None
        payload = _DecisionPayload.model_validate_json(event.payload_json)
        if payload.decision == OrchestratorDecision.CLASSIFIED_BLOCK.value:
            return event.created_at
    return None


def _agent_ran_between(session: Session, ticket_id: str, start: datetime, end: datetime) -> bool:
    return (
        session.exec(
            select(AgentRun.id)
            .where(AgentRun.ticket_id == ticket_id)
            .where(col(AgentRun.started_at) > start, col(AgentRun.started_at) < end)
            .limit(1)
        ).first()
        is not None
    )


def work_blocks_from_unavailable_gates(session: Session) -> list[MisfiledBlock]:
    """Blocked tickets filed as WORK whose block is a gate that never ran."""
    found: list[MisfiledBlock] = []
    blocked = session.exec(
        select(Ticket).where(
            Ticket.state == TicketState.BLOCKED, Ticket.block_kind == BlockKind.WORK
        )
    ).all()
    for ticket in blocked:
        gates = _latest(session, ticket.id, EventType.GATE_EVALUATED)
        if not gates:
            continue
        gate = _GatePayload.model_validate_json(gates[0].payload_json)
        if not gate.outcome.reached_no_verdict:
            continue
        if gate.stage_key != ticket.workflow_stage_key:
            continue
        classified_at = _classified_after(session, ticket.id, gates[0].created_at)
        if classified_at is None:
            continue
        if _agent_ran_between(session, ticket.id, gates[0].created_at, classified_at):
            continue
        found.append(MisfiledBlock(ticket, gate.stage_key, classified_at))
    return found


def refile_as_harness(session: Session, ticket_ids: Sequence[str]) -> int:
    """Re-file each still-misfiled block as HARNESS, saying so in its history."""
    wanted = set(ticket_ids)
    refiled = 0
    for block in work_blocks_from_unavailable_gates(session):
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
                f"Block on '{block.stage_key}' re-filed as harness: the gate there never "
                "ran, so it found nothing in the work."
            ),
            evidence={"block_kind": BlockKind.HARNESS.value, "was": BlockKind.WORK.value},
        )
        logger.warning(
            "Re-filed %s's block on '%s' from work to harness: its gate never ran",
            block.ticket.external_id or block.ticket.id,
            block.stage_key,
        )
        refiled += 1
    session.commit()
    return refiled
