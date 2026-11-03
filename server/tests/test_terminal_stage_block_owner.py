"""A refused landing is a person's block, and every surface says so.

lg-machine-resource-1923's landing was refused because the tree's tests failed.
The repair turn refused it ("recorded outside an orchestration run", which was
not even the reason), while the ticket page read "Blocked · agent can fix:
running its blocked stage again starts the fix". No agent runs on the terminal
stage; running it again lands the same tree and fails the same way.
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    BlockKind,
    DomainEvent,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.artifact_service import block_ticket_for_unresolved_blocker
from loregarden.services.autopilot_retry import retry_reason
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, col, select
from tests.test_block_repair import IMPLEMENT, REVIEW, ticket_fixture  # noqa: F401

LAND_REFUSED = (
    "Could not land loregarden/x on integration/y: the tests failed\n"
    "exited 1 · $ uv run --directory server pytest -q -n auto"
)


def _block_on(session: Session, ticket: Ticket, stage_key: str) -> None:
    """The shape `landing._block` leaves: earlier stages done, this one BLOCKED."""
    _, stages = resolve_ticket_stages(session, ticket)
    instance = workflow_instance_for(session, ticket.id)
    for key in (IMPLEMENT, REVIEW):
        if key != stage_key:
            set_stage_status(ticket, instance, stages, key, StageStatus.DONE)
    set_stage_status(ticket, instance, stages, stage_key, StageStatus.BLOCKED)
    ticket.workflow_stage_key = stage_key
    session.add(instance)
    block_ticket_for_unresolved_blocker(session, ticket, entry=LAND_REFUSED, stage_key=stage_key)
    session.refresh(ticket)


def _escalation_reason(session: Session, ticket: Ticket) -> str:
    events = session.exec(
        select(DomainEvent)
        .where(col(DomainEvent.ticket_id) == ticket.id)
        .order_by(col(DomainEvent.created_at))
    ).all()
    payloads = [json.loads(e.payload_json or "{}") for e in events]
    reasons = [
        p.get("reason", "")
        for p in payloads
        if p.get("decision") == OrchestratorDecision.REPAIR_ESCALATED.value
    ]
    assert reasons, "a block that got no repair turn records why"
    return reasons[-1]


def test_a_refused_landing_is_not_one_a_re_run_clears(db_session: Session, ticket: Ticket):
    _block_on(db_session, ticket, "done")

    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.WORK, "classified from the message, as on live"
    assert retry_reason(db_session, ticket) is None


def test_its_refusal_names_the_terminal_stage(db_session: Session, ticket: Ticket):
    _block_on(db_session, ticket, "done")

    reason = _escalation_reason(db_session, ticket)
    assert "terminal stage" in reason and "outside an orchestration" not in reason, reason


def test_the_same_block_on_an_agent_stage_still_offers_its_re_run(
    db_session: Session, ticket: Ticket
):
    """The control: an agent stage's work block keeps its unspent repair turn."""
    _block_on(db_session, ticket, REVIEW)

    assert retry_reason(db_session, ticket) is not None
