"""Which human gate a ticket's person-judged criteria are put to, and who may not sign it.

A criterion settled ``PERSON`` (`criterion_tables`) is answered by a person at
the ticket's human gate. That only means something if the gate is still ahead,
opens for a person, and is not signed by automation in their place.
"""

from __future__ import annotations

from loregarden.models.domain import (
    CriterionDisposition,
    CriterionSettlement,
    StageStatus,
    Ticket,
    WorkflowStageDef,
)
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.criterion_gate_stages import gate_of, settler_of
from loregarden.services.criterion_ledger import settlements_of
from loregarden.services.studio_routing import is_agentless_stage, is_terminal_stage
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session

_CLOSED = (StageStatus.DONE, StageStatus.WONT_DO)


def person_gate_of(
    stages: list[WorkflowStageDef], stage_map: dict[str, StageStatus]
) -> WorkflowStageDef | None:
    """The open stage where a person answers, or None when this ticket has none left.

    The first agentless gate after the criteria are settled; else the gate
    itself, when a verify stage settles ahead of it and it asks for a sign-off.
    """
    settler, gate = settler_of(stages), gate_of(stages)
    if settler is None or gate is None:
        return None
    ahead = [
        s
        for s in sorted(stages, key=lambda s: s.order)
        if s.order > settler.order
        and not is_terminal_stage(s)
        and s.exit_actions_enabled
        and s.exit_actions
        and stage_map.get(s.key) not in _CLOSED
    ]
    agentless = next((s for s in ahead if is_agentless_stage(s)), None)
    return agentless or next((s for s in ahead if s.key == gate.key), None)


def ticket_person_gate(session: Session, ticket: Ticket) -> WorkflowStageDef | None:
    """`person_gate_of` for ``ticket`` as it stands now."""
    _, stages = resolve_ticket_stages(session, ticket)
    instance = workflow_instance_for(session, ticket.id)
    if not stages or instance is None:
        return None
    return person_gate_of(stages, parse_stage_map(instance, stages))


def person_settlements(session: Session, ticket: Ticket) -> list[CriterionSettlement]:
    """The ticket's criteria a person answers, in the ticket's order."""
    settled = settlements_of(session, ticket)
    return [
        settled[c]
        for c in load_criteria(ticket.acceptance_criteria_json)
        if c in settled and settled[c].disposition is CriterionDisposition.PERSON
    ]


def is_a_persons_gate(session: Session, ticket: Ticket, stage_key: str) -> bool:
    """Whether a person must answer ``stage_key``: criteria wait on them there."""
    if not person_settlements(session, ticket):
        return False
    gate = ticket_person_gate(session, ticket)
    return gate is not None and gate.key == stage_key


def refuse_skipping_a_persons_gate(session: Session, ticket: Ticket, stage_key: str) -> None:
    """Raise when ``stage_key`` is the gate a person's criteria wait at."""
    if is_a_persons_gate(session, ticket, stage_key):
        raise ValueError(
            f"Stage '{stage_key}' is where a person answers criteria of this ticket and "
            "cannot be skipped. Settle those criteria another way first."
        )


__all__ = [
    "is_a_persons_gate",
    "person_gate_of",
    "person_settlements",
    "refuse_skipping_a_persons_gate",
    "ticket_person_gate",
]
