"""A gate that could not run is never a work block (lg-machine-resource-2112).

lg-reference-sources-2018's survey→done gate waited its full 30-minute budget
behind an orderly line of test suites on 2026-11-04, gave its place up, and
came back UNAVAILABLE. Recovery blocked it with the pointer "Transition gate
failed at 'survey'", the text classifier read that as WORK, and the escalation
said "the gate's automatic fixes ... are spent" when no fix tier had run.

- the wait: a gate keeps its place while the line moves, and gives up only when
  nothing ahead has finished for its stall window;
- the block: an UNAVAILABLE gate that does block is declared HARNESS, says the
  gate did not run, and gives the true reason no repair was offered.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from loregarden.models.domain import (
    BlockKind,
    CapacityPool,
    DockerFootprint,
    DockerLeaseEndReason,
    DomainEvent,
    EventType,
    GateOutcome,
    OrchestrationRun,
    OrchestratorDecision,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.capacity_line import wait_while_line_moves
from loregarden.services.docker_leases import release_lease
from loregarden.services.docker_ledger import as_utc
from loregarden.services.gate_capacity import GateCapacityUnavailable, acquire_gate_capacity
from loregarden.services.gate_recovery import GATE_DID_NOT_RUN_REASON, GateDecision
from loregarden.services.gate_runner import GateRunResult
from loregarden.services.invariant_registry import UNAVAILABLE_GATES_ARE_NOT_WORK
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_orchestration_run, make_ticket, make_workspace
from tests.history_helpers import decision_payloads
from tests.test_capacity_host_pool import _reserve, _set_ceiling

# ---- the wait -------------------------------------------------------------


class _ScriptedLine:
    """A clock whose sleeps end the leases scheduled for that moment."""

    def __init__(self, session: Session, releases: list[tuple[float, str]]) -> None:
        self.session = session
        self.releases = sorted(releases)
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        while self.releases and self.releases[0][0] <= self.now:
            _, lease_id = self.releases.pop(0)
            release_lease(self.session, lease_id, reason=DockerLeaseEndReason.RELEASED)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        _set_ceiling(session, CapacityPool.HOST, cpus=8, memory_mb=16384, leases=8)
        yield session


@pytest.fixture(name="full_line")
def full_line_fixture(session):
    """Two suites hold the machine, one waits ahead, and the gate's claim is last."""
    first = _reserve(session, CapacityPool.HOST, label="suite-a", cpus=4)
    second = _reserve(session, CapacityPool.HOST, label="suite-b", cpus=4)
    ahead = _reserve(session, CapacityPool.HOST, label="suite-c", cpus=4)
    gate = _reserve(session, CapacityPool.HOST, label="gates survey_to_done", cpus=4)
    assert first.granted and second.granted
    assert not ahead.granted and not gate.granted
    return first, second, gate


def test_a_line_that_keeps_moving_is_waited_out_past_the_stall_window(session, full_line):
    first, second, gate = full_line
    line = _ScriptedLine(session, [(0.4, first.lease_id), (0.8, second.lease_id)])

    granted = wait_while_line_moves(
        session, gate, stall_seconds=0.5, clock=line.clock, sleep=line.sleep
    )

    assert granted.granted, granted.message
    assert line.now > 0.5, "the total wait outlasted the stall window"


def test_a_line_where_nothing_finishes_gives_up_naming_the_holders(session, full_line):
    _first, _second, gate = full_line
    line = _ScriptedLine(session, [])

    stalled = wait_while_line_moves(
        session, gate, stall_seconds=0.5, clock=line.clock, sleep=line.sleep
    )

    assert not stalled.granted
    assert 0.5 <= line.now < 1.0
    assert "suite-a" in stalled.message and "suite-b" in stalled.message


def test_a_gate_waiting_in_line_keeps_its_orchestration_alive(session, monkeypatch):
    """Lease renewal happens only at stage boundaries. A gate sits between two of
    them, so a long wait would read as the run walking away."""
    workspace = make_workspace(session, slug="gate-line")
    ticket = make_ticket(session, workspace_id=workspace.id)
    orch_run = make_orchestration_run(session, workspace_id=workspace.id, ticket_id=ticket.id)
    stale = utcnow() - timedelta(hours=2)
    orch_run.last_seen_at = stale
    session.add(orch_run)
    session.commit()
    _reserve(session, CapacityPool.HOST, label="suite-a", cpus=8)

    with pytest.raises(GateCapacityUnavailable, match="the line stopped moving"):
        acquire_gate_capacity(
            session,
            DockerFootprint.HEAVY,
            ticket=ticket,
            workspace=workspace,
            transition="survey_to_done",
            ttl_seconds=60,
            wait_seconds=0.3,
            orchestration_run_id=orch_run.id,
        )

    session.expire_all()
    renewed = session.get(OrchestrationRun, orch_run.id).last_seen_at
    assert renewed is not None and as_utc(renewed) > stale + timedelta(hours=1)


# ---- the block ------------------------------------------------------------


def _unavailable_gate_blocks(db_session: Session, message: str) -> Ticket:
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ticket = db_session.exec(
        select(Ticket).where(
            Ticket.workspace_id == workspace.id, Ticket.work_item_type == WorkItemType.TASK
        )
    ).first()
    instance, stages = OrchestrationService(db_session)._resolve_stages(ticket)
    from_stage = ticket.workflow_stage_key
    stage_def = next(stage for stage in stages if stage.key == from_stage)
    orch_run = make_orchestration_run(
        db_session, workspace_id=workspace.id, ticket_id=ticket.id, run_code="unavailable"
    )
    result = GateRunResult(ok=False, outcome=GateOutcome.UNAVAILABLE, message=message)
    with mock.patch(
        "loregarden.services.gate_recovery.run_transition_gates", return_value=result
    ) as gates:
        decision = BuiltinOrchestrator(db_session).gates.run_gates_with_autofix(
            ticket,
            OrchestrationProfile(slug="loregarden"),
            stage_def,
            instance,
            stages,
            orch_run,
            from_stage=from_stage,
            to_stage="next",
        )
    assert gates.called, "the patched runner is the one recovery used"
    assert decision is GateDecision.BLOCKED
    db_session.refresh(ticket)
    return ticket


def test_an_unavailable_gate_blocks_as_harness_with_the_true_reason(db_session):
    ticket = _unavailable_gate_blocks(
        db_session,
        "Gate commands for survey_to_done did not run: no heavy host capacity "
        "(the line stopped moving) — nothing ahead in the line finished for 1800s",
    )

    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.HARNESS
    assert "could not run, so nothing was checked" in (ticket.blocking_issues or "")
    assert "Transition gate failed" not in (ticket.blocking_issues or "")
    (escalation,) = [
        payload
        for payload in decision_payloads(db_session, ticket.id)
        if payload["decision"] == "repair_escalated"
    ]
    assert escalation["reason"] == GATE_DID_NOT_RUN_REASON
    assert "are spent" not in escalation["reason"]


# ---- the invariant --------------------------------------------------------


def _filed_as_work(db_session: Session) -> Ticket:
    """The state as it reached the live database before the guard."""
    ticket = _unavailable_gate_blocks(db_session, "Gates did not run: the ledger is unreadable")
    ticket.block_kind = BlockKind.WORK
    db_session.add(ticket)
    db_session.commit()
    return ticket


def test_the_invariant_refiles_a_gate_that_never_ran_as_harness(db_session):
    ticket = _filed_as_work(db_session)

    (violation,) = UNAVAILABLE_GATES_ARE_NOT_WORK.detect(db_session)
    assert violation.subject_id == ticket.id

    assert UNAVAILABLE_GATES_ARE_NOT_WORK.repair(db_session, [violation]) == 1
    db_session.refresh(ticket)
    assert ticket.block_kind is BlockKind.HARNESS
    assert UNAVAILABLE_GATES_ARE_NOT_WORK.detect(db_session) == []


def test_a_later_stage_attempts_own_block_is_not_the_gates(db_session):
    """The control: an agent run after the gate is a new attempt, and the WORK
    block it ends in is its own, whatever the last gate said."""
    ticket = _filed_as_work(db_session)
    make_agent_run(
        db_session,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        started_at=utcnow(),
    )
    record_orchestrator_decision(
        db_session,
        ticket,
        decision=OrchestratorDecision.CLASSIFIED_BLOCK,
        stage_key=ticket.workflow_stage_key,
        reason="Block classified as work.",
    )
    db_session.commit()

    assert UNAVAILABLE_GATES_ARE_NOT_WORK.detect(db_session) == []


def test_a_work_block_after_a_gate_that_failed_is_the_works(db_session):
    """The control on the outcome: a gate that ran and found something is work."""
    ticket = _filed_as_work(db_session)
    (gate,) = db_session.exec(
        select(DomainEvent).where(
            DomainEvent.ticket_id == ticket.id, DomainEvent.type == EventType.GATE_EVALUATED
        )
    ).all()
    gate.payload_json = gate.payload_json.replace('"unavailable"', '"failed"')
    db_session.add(gate)
    db_session.commit()

    assert UNAVAILABLE_GATES_ARE_NOT_WORK.detect(db_session) == []
