"""Each agent-run dispatch carries an idempotency key, so a replay starts one run.

lg-milestone-that-2490: a dispatch retried after a crash, restart or timeout
could start a second agent on the same stage.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.agents.executors import stage_turn
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    EventType,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestratorDecision,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.models.domain.enums import DispatchSurface, utcnow
from loregarden.services import dispatch_key as dispatch_key_module
from loregarden.services.dispatch_key import dispatched_run
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket
from tests.test_rework_resume import _run as rework_run
from tests.test_rework_resume import _stream


def _stage(key: str, order: int, agent_id: str = "", **extra) -> WorkflowStageDef:
    return WorkflowStageDef(
        key=key, name=key, agent_id=agent_id, order=order, exit_actions_enabled=False, **extra
    )


@pytest.fixture
def orchestrated(db_session: Session, tmp_path):
    """A ticket at `implement`, driven by a live orchestration run."""
    stages = [_stage("implement", 1, "backend_implementer"), _stage("done", 2, terminal=True)]
    template = WorkflowTemplate(
        slug=f"dk-{uuid4().hex[:8]}",
        name="Dispatch key",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps([{"from": "implement", "to": "done", "when": "pass"}]),
    )
    db_session.add(template)
    db_session.commit()
    workspace = Workspace(
        slug=f"dk-{uuid4().hex[:8]}",
        name="Dispatch key",
        repo_path=str(tmp_path),
        workflow_template_id=template.id,
    )
    db_session.add(workspace)
    db_session.commit()
    ticket = Ticket(
        external_id=f"dk-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title="Dispatch key",
        state=TicketState.IN_PROGRESS,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.PENDING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="implement",
            stages_json=initial_stages_json(stages),
        )
    )
    orch = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        current_stage_key="implement",
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(orch)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket, orch


def _dispatch(session: Session, ticket: Ticket, orch: OrchestrationRun | None, **kwargs):
    session.refresh(ticket)
    return OrchestrationService(session).start_run(
        ticket,
        stage_key="implement",
        orchestration_run_id=orch.id if orch is not None else None,
        **kwargs,
    )


def _runs(session: Session, ticket: Ticket) -> list[AgentRun]:
    return list(session.exec(select(AgentRun).where(AgentRun.ticket_id == ticket.id)).all())


def _replays(session: Session, ticket: Ticket) -> list[dict]:
    events = session.exec(
        select(DomainEvent).where(
            DomainEvent.ticket_id == ticket.id,
            DomainEvent.type == EventType.ORCHESTRATOR_DECISION,
        )
    ).all()
    payloads = [{**json.loads(e.payload_json), "run_id": e.run_id} for e in events]
    return [p for p in payloads if p["decision"] == OrchestratorDecision.DISPATCH_REPLAYED]


def _settle(session: Session, run: AgentRun) -> None:
    OrchestrationService(session).complete_run(
        run, status=RunStatus.FAILED, stderr="boom", advance_workflow=False
    )


# ---- keyed by orchestration run, stage and attempt -----------------------------


def test_a_dispatch_is_keyed_by_its_orchestration_stage_agent_and_attempt(db_session, orchestrated):
    ticket, orch = orchestrated

    run = _dispatch(db_session, ticket, orch)

    assert run.dispatch_key == f"orch:{orch.id}|implement|backend_implementer|||1"


def test_a_dispatch_replayed_after_a_crash_gets_the_one_run(db_session, orchestrated):
    """AC3. The dispatcher committed the run and died before acting on it; on
    restart it dispatches the same stage again."""
    ticket, orch = orchestrated
    first = _dispatch(db_session, ticket, orch)
    first_id, ticket_id, orch_id = first.id, ticket.id, orch.id
    db_session.expunge_all()  # the crash: nothing the dispatcher held survives
    ticket = db_session.get(Ticket, ticket_id)
    first = db_session.get(AgentRun, first_id)

    replayed = _dispatch(
        db_session,
        ticket,
        db_session.get(OrchestrationRun, orch_id),
        dispatch_surface=DispatchSurface.QUEUE,
    )

    assert replayed.id == first.id
    assert [r.id for r in _runs(db_session, ticket)] == [first.id]
    [event] = _replays(db_session, ticket)
    assert event["run_id"] == first.id
    assert event["dispatch_key"] == first.dispatch_key
    assert event["surface"] == DispatchSurface.QUEUE.value
    assert first.run_code in event["reason"]


def test_a_dispatch_after_the_run_settled_is_the_next_attempt(db_session, orchestrated):
    ticket, orch = orchestrated
    first = _dispatch(db_session, ticket, orch)
    _settle(db_session, first)

    second = _dispatch(db_session, ticket, orch)

    assert second.id != first.id
    assert second.dispatch_key.endswith("|2")
    assert _replays(db_session, ticket) == []


def test_parallel_members_of_one_stage_are_separate_lanes(db_session, orchestrated):
    ticket, orch = orchestrated

    one = _dispatch(db_session, ticket, orch, agent_id="architecture_reviewer")
    other = _dispatch(db_session, ticket, orch, agent_id="static_qa")

    assert one.id != other.id
    assert len(_runs(db_session, ticket)) == 2


def test_a_dispatch_outside_an_orchestration_is_keyed_by_its_ticket(db_session, orchestrated):
    ticket, _ = orchestrated

    first = _dispatch(db_session, ticket, None)
    again = _dispatch(db_session, ticket, None)

    assert first.dispatch_key.startswith(f"ticket:{ticket.id}|implement|")
    assert again.id == first.id


# ---- an uncertain acceptance is read, never dispatched again ---------------------


def test_a_run_whose_lease_lapsed_is_still_returned_not_dispatched_again(db_session, orchestrated):
    """Whether its supervisor is alive is the lease sweep's to settle; until it
    does, the dispatch is resolved by reading that run."""
    ticket, orch = orchestrated
    first = _dispatch(db_session, ticket, orch)
    first.last_seen_at = utcnow() - timedelta(hours=2)
    db_session.add(first)
    db_session.commit()

    replayed = _dispatch(db_session, ticket, orch)

    assert replayed.id == first.id
    assert len(_runs(db_session, ticket)) == 1


def test_two_dispatches_racing_past_the_read_make_one_run(db_session, orchestrated):
    """The unique index, not the read, is what holds: a dispatch that missed the
    winner's row on its read collides on insert and gets the winner back."""
    ticket, orch = orchestrated
    winner = _dispatch(db_session, ticket, orch)
    misses_once = mock.Mock(side_effect=[None, winner])

    with mock.patch.object(dispatch_key_module, "dispatched_run", misses_once):
        loser = _dispatch(db_session, ticket, orch)

    assert loser.id == winner.id
    assert misses_once.call_count == 2  # the read missed; the insert collided
    assert len(_runs(db_session, ticket)) == 1
    assert dispatched_run(db_session, winner.dispatch_key).id == winner.id


# ---- a returned run is executed once ----------------------------------------------


def test_a_run_is_executed_once_however_often_it_is_handed_over(db_session):
    ticket = make_workspace_ticket(db_session, "dispatch-key-exec")
    run = rework_run(
        db_session, ticket, repo_root=Path("/nowhere"), code="run_1", status=RunStatus.RUNNING
    )

    with mock.patch.object(
        stage_turn, "run_print_mode", return_value=(_stream(), "", RunStatus.SUCCEEDED)
    ) as spawn:
        CliAgentExecutor(db_session).execute(run, ticket)
        again = CliAgentExecutor(db_session).execute(run, ticket)

    assert spawn.call_count == 1
    assert again.execution_claimed_at is not None
