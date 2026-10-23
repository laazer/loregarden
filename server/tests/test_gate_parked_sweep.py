"""Tickets an exhausted gate parked before it blocked are blocked by the sweep.

The shape is the one the old `_block_after_gate_failure` left: the ticket in
progress at a pending stage, its latest orchestration SUCCEEDED with "Transition
gate failed at '<stage>'; rerouted for rework", and nothing behind it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.gate_parked_sweep import (
    PARKED_MESSAGE,
    PARKED_POINTER,
    block_parked,
    parked_by_gate,
)
from loregarden.services.invariant_registry import EXHAUSTED_GATES_BLOCK
from loregarden.services.invariants import run_invariant
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import parse_stage_map, stages_up_to_done_json
from sqlmodel import Session, select

STAGE = "test-break"
T0 = datetime(2026, 10, 23, 12, 26, tzinfo=timezone.utc)


def _orchestration(
    session: Session, ticket: Ticket, *, message: str, created: datetime
) -> OrchestrationRun:
    run = OrchestrationRun(
        run_code=f"orch_{created.timestamp():.0f}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.SUCCEEDED,
        error_message=message,
        created_at=created,
        started_at=created,
        finished_at=created + timedelta(minutes=12),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@pytest.fixture(name="parked")
def parked_fixture(db_session: Session, tmp_path: Path) -> Ticket:
    """A ticket left exactly as the old writer left it."""
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).one()
    workspace = Workspace(slug="parked", name="parked", repo_path=str(tmp_path))
    db_session.add(workspace)
    db_session.commit()
    ticket = Ticket(
        external_id="parked-1",
        workspace_id=workspace.id,
        title="parked",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=STAGE,
        workflow_stage_status=StageStatus.PENDING,
        blocking_issues=f"Transition gate failed at '{STAGE}' — see the Timeline tab for details.",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=STAGE,
            stages_json=stages_up_to_done_json(get_template_stages(template), "test-design"),
        )
    )
    db_session.commit()
    _orchestration(db_session, ticket, message=PARKED_MESSAGE.format(stage=STAGE), created=T0)
    return ticket


def _stage_status(session: Session, ticket: Ticket) -> StageStatus:
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    _, stages = resolve_ticket_stages(session, ticket)
    return parse_stage_map(instance, stages)[STAGE]


def test_a_parked_ticket_is_found(db_session: Session, parked: Ticket):
    assert [item.ticket.id for item in parked_by_gate(db_session)] == [parked.id]


def test_blocking_it_blocks_ticket_and_stage_with_the_pointer(db_session: Session, parked: Ticket):
    assert block_parked(db_session, parked_by_gate(db_session)) == 1

    db_session.refresh(parked)
    assert parked.state == TicketState.BLOCKED
    assert _stage_status(db_session, parked) == StageStatus.BLOCKED
    assert parked.blocking_issues == PARKED_POINTER.format(stage=STAGE)
    assert parked_by_gate(db_session) == [], "a blocked ticket is no longer parked"


def test_a_ticket_restarted_since_is_not_parked(db_session: Session, parked: Ticket):
    _orchestration(db_session, parked, message="", created=T0 + timedelta(hours=1))

    assert parked_by_gate(db_session) == []


def test_a_ticket_that_moved_to_another_stage_is_not_parked(db_session: Session, parked: Ticket):
    parked.workflow_stage_key = "implement"
    db_session.add(parked)
    db_session.commit()

    assert parked_by_gate(db_session) == []


def test_a_closed_ticket_is_not_parked(db_session: Session, parked: Ticket):
    parked.state = TicketState.DONE
    db_session.add(parked)
    db_session.commit()

    assert parked_by_gate(db_session) == []


def test_the_invariant_repairs_the_backlog_and_converges(db_session: Session, parked: Ticket):
    run_invariant(db_session, EXHAUSTED_GATES_BLOCK)

    db_session.refresh(parked)
    assert parked.state == TicketState.BLOCKED
    assert EXHAUSTED_GATES_BLOCK.detect(db_session) == []
