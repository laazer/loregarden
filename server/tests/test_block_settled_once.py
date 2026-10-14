"""A block is settled once, with the drive it happened in, under its true reason.

Over the 30 days to 2026-10-14, 56 of 93 escalations read "the block was
recorded outside an orchestration run". 21 were a second settlement of a block
the run's own completion had already settled, on top of the real reason; most
of the rest were inside a live drive, at a rework cap or a dispatch budget.
"""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.models.domain import (
    BlockOrigin,
    OrchestrationDriver,
    OrchestrationRunStatus,
    OrchestratorDecision,
    RunStatus,
    Ticket,
    TicketState,
    WorkflowStageDef,
)
from loregarden.services.artifact_service import block_ticket_for_unresolved_blocker
from loregarden.services.block_repair import repair_pinned
from loregarden.services.block_settlement import RepairPolicy, sweep_unclassified_blocks
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.run_completion import advance_stage_after_run
from loregarden.services.seed import seed_database
from sqlmodel import Session, select
from tests.history_helpers import decision_kinds, decision_payloads

STAGE = "testing"


def _ticket(session: Session) -> Ticket:
    ticket = session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket is not None
    return ticket


@pytest.fixture
def session(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        yield session


@pytest.fixture
def drive(session):
    """A live builtin drive on STAGE with its run in flight."""
    ticket = _ticket(session)
    callbacks = OrchestrationCallbackService(session)
    orch_run = callbacks.start_orchestration_run(
        ticket, driver=OrchestrationDriver.BUILTIN_AUTOPILOT, profile_slug="default"
    )
    run = OrchestrationService(session).start_run(
        ticket, stage_key=STAGE, orchestration_run_id=orch_run.id
    )
    run.status = RunStatus.RUNNING
    session.add(run)
    session.commit()
    return ticket, orch_run, run, callbacks


def _escalations(session, ticket_id) -> list[dict]:
    return [
        p
        for p in decision_payloads(session, ticket_id)
        if p["decision"] == OrchestratorDecision.REPAIR_ESCALATED
    ]


def test_a_withheld_repair_needs_a_reason():
    with pytest.raises(ValueError):
        RepairPolicy.withheld("")


# ---- 4: settled once ------------------------------------------------------------------


def test_a_block_its_run_already_settled_is_not_settled_again(session, drive):
    ticket, orch_run, run, callbacks = drive
    orch_run.auto_repair = False
    run.status = RunStatus.FAILED
    session.add_all([orch_run, run])
    session.commit()
    advance_stage_after_run(
        OrchestrationService(session), ticket, run, None, RunStatus.FAILED, "assertion failed"
    )
    session.refresh(ticket)
    assert ticket.block_kind is not None, "the premise: completion settled it"
    before = _escalations(session, ticket.id)

    callbacks.block_ticket(
        orch_run,
        ticket,
        origin=BlockOrigin.AGENT,
        repair=RepairPolicy.withheld("a second, wrong reason"),
        stage_key=STAGE,
        message="assertion failed",
    )

    after = _escalations(session, ticket.id)
    assert after == before
    assert all("outside an orchestration run" not in e["reason"] for e in after)


# ---- 3: settled with the drive, under the caller's reason -------------------------------


def test_a_withheld_block_records_the_callers_reason(session, drive):
    ticket, orch_run, _, callbacks = drive
    callbacks.block_ticket(
        orch_run,
        ticket,
        origin=BlockOrigin.CONTROL_PLANE,
        repair=RepairPolicy.withheld("'testing' reached the rework loop cap."),
        stage_key=STAGE,
        message="rework cap",
    )
    [escalation] = _escalations(session, ticket.id)
    assert escalation["reason"] == "'testing' reached the rework loop cap."
    session.refresh(orch_run)
    assert orch_run.status is OrchestrationRunStatus.BLOCKED


def test_a_repairable_block_takes_its_repair_turn_and_the_drive_goes_on(session, drive):
    ticket, orch_run, run, callbacks = drive
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()

    callbacks.block_ticket(
        orch_run,
        ticket,
        origin=BlockOrigin.AGENT,
        repair=RepairPolicy.offer(),
        stage_key=STAGE,
        message="Parallel stage failed: static_qa crashed",
    )

    session.refresh(ticket)
    session.refresh(orch_run)
    assert repair_pinned(ticket, STAGE)
    assert OrchestratorDecision.DISPATCHED_REPAIR.value in decision_kinds(session, ticket.id)
    assert orch_run.status is OrchestrationRunStatus.RUNNING
    assert ticket.state is not TicketState.BLOCKED
    assert _escalations(session, ticket.id) == []


# ---- 3: a blocker recorded mid-run waits for that run --------------------------------


def test_a_blocker_checkpoint_mid_run_is_left_to_the_runs_completion(session, drive):
    ticket, _, _, _ = drive
    block_ticket_for_unresolved_blocker(session, ticket, entry="cannot reach X", stage_key=STAGE)
    session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is None
    assert decision_kinds(session, ticket.id) == []
    # The periodic sweep leaves it alone too, while the run is going.
    assert sweep_unclassified_blocks(session) == 0


def test_a_blocker_checkpoint_with_no_run_going_is_settled_now(session, drive):
    ticket, _, run, _ = drive
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    block_ticket_for_unresolved_blocker(session, ticket, entry="cannot reach X", stage_key=STAGE)
    session.refresh(ticket)
    assert ticket.block_kind is not None
    assert len(_escalations(session, ticket.id)) == 1


def test_a_failed_parallel_stage_with_a_repair_armed_keeps_the_drive_going(session, drive):
    """The orchestrator's parallel branch: it used to end the drive unconditionally."""
    ticket, orch_run, run, _ = drive
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    stage_def = WorkflowStageDef(key=STAGE, name=STAGE, stage_type="parallel")
    orchestrator = BuiltinOrchestrator(session)
    with mock.patch.object(
        orchestrator, "_execute_parallel_stage", return_value=(False, "static_qa crashed")
    ):
        stop = orchestrator._run_parallel_stage_or_stop(
            ticket, orch_run, stage_def, STAGE, auto_approve=False, resuming=False
        )
    session.refresh(ticket)
    assert stop is False
    assert repair_pinned(ticket, STAGE)
