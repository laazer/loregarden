"""`TicketStateChanged` records a stage-map close, and says who moved the ticket.

Two holes in the event log, found tracing why lg-milestone-that-2196 seemed slow
to finish and why the next ticket did not start:

- a close through the stage map goes through `ticket_state_service.derive`,
  which logged nothing, so no ticket's history ever showed it reaching `done`
  (96 cleared-landing finishes on the live database, 0 events among them);
- `start_ticket` stamped `actor: "human"` on every ticket an orchestration
  started, so a parent's run and the autopilot both read as a person's doing.
"""

from __future__ import annotations

import json

from loregarden.models.domain import DomainEvent, EventType, StageStatus, TicketState
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_state import (
    parse_stage_map,
    reconcile_workflow_state,
    serialize_stage_map,
)
from sqlmodel import Session, col, select
from tests.factories import make_workspace_ticket


def _state_events(session: Session, ticket_id: str) -> list[dict]:
    session.expire_all()
    rows = session.exec(
        select(DomainEvent)
        .where(DomainEvent.ticket_id == ticket_id)
        .where(DomainEvent.type == EventType.TICKET_STATE_CHANGED)
        .order_by(col(DomainEvent.created_at))
    ).all()
    return [json.loads(row.payload_json) for row in rows]


def _finish_every_stage(session: Session, orch: OrchestrationService, ticket) -> None:
    orch.build_stage_views(ticket)
    instance, stages = orch._resolve_stages(ticket)
    done = dict.fromkeys(parse_stage_map(instance, stages), StageStatus.DONE)
    instance.stages_json = serialize_stage_map(done, stages, notes={})
    session.add(instance)
    session.commit()


def test_a_close_through_the_stage_map_logs_its_move_to_done(db_session) -> None:
    ticket = make_workspace_ticket(db_session, "lg-state-events-1")
    orch = OrchestrationService(db_session)
    _finish_every_stage(db_session, orch, ticket)

    orch.build_stage_views(ticket)

    db_session.refresh(ticket)
    assert ticket.state is TicketState.DONE
    assert _state_events(db_session, ticket.id)[-1] == {"state": "done", "actor": "workflow"}


def test_reconciling_an_unchanged_state_logs_nothing_more(db_session) -> None:
    ticket = make_workspace_ticket(db_session, "lg-state-events-2")
    orch = OrchestrationService(db_session)
    _finish_every_stage(db_session, orch, ticket)
    orch.build_stage_views(ticket)
    before = _state_events(db_session, ticket.id)

    orch.build_stage_views(ticket)

    assert _state_events(db_session, ticket.id) == before


def test_the_event_commits_with_the_state_or_not_at_all(db_session) -> None:
    ticket = make_workspace_ticket(db_session, "lg-state-events-3")
    orch = OrchestrationService(db_session)
    _finish_every_stage(db_session, orch, ticket)
    before = _state_events(db_session, ticket.id)
    instance, stages = orch._resolve_stages(ticket)

    reconcile_workflow_state(ticket, instance, stages)
    db_session.rollback()

    db_session.refresh(ticket)
    assert ticket.state is not TicketState.DONE
    assert _state_events(db_session, ticket.id) == before


def test_a_ticket_an_orchestration_starts_is_not_labelled_a_persons_doing(db_session) -> None:
    ticket = make_workspace_ticket(db_session, "lg-state-events-4", state=TicketState.BACKLOG)

    OrchestrationService(db_session).start_ticket(ticket)

    assert _state_events(db_session, ticket.id)[-1] == {
        "state": "in_progress",
        "actor": "orchestrator",
    }
