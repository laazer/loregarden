"""A finished stage is gated before the next one, whoever finished it.

lmkr-execution-leaves-11, 2026-11-23: a reload killed the driver while
`implement` ran. The agent finished, the stage read DONE, and the next run went
straight to `testing` past a gate that failed when it finally ran.
"""

import json
from datetime import datetime, timedelta
from unittest import mock

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    DomainEvent,
    EventType,
    GateOutcome,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.gate_runner import GateRunResult
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.owed_gate import owed_gate
from loregarden.services.ungated_stage_invariant import stages_left_ungated
from loregarden.services.workflow_state import (
    parse_stage_map,
    set_stage_status,
    stages_up_to_done_json,
)
from loregarden.testing.factories import make_workspace, operator_judgment_gate_payload
from sqlmodel import Session, select

#: The stage the agent finished, and the one after it, in `blobert-tdd`.
FINISHED, NEXT = "test-design", "test-break"


def _passing_agent(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
    return self.orchestration.complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=(
            "Done.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
            '{"status": "pass", "confidence": 0.95}\n<<<END_STAGE_REPORT>>>\n'
        ),
        stderr="",
    )


@pytest.fixture(name="agents", autouse=True)
def agents_fixture():
    """Every agent run passes at once; the stages dispatched are the AgentRun rows."""
    with mock.patch.object(CliAgentExecutor, "execute", _passing_agent):
        yield


@pytest.fixture(name="finished")
def finished_fixture(db_session: Session, tmp_path) -> Ticket:
    """A ticket whose last stage an agent finished after its driver was gone."""
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).one()
    workspace = make_workspace(db_session, slug="owed-gate", repo_path=str(tmp_path))
    ticket = Ticket(
        external_id="owed-gate-1",
        workspace_id=workspace.id,
        title="Owed gate",
        state=TicketState.IN_PROGRESS,
        workflow_stage_key=NEXT,
        workflow_stage_status=StageStatus.PENDING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=NEXT,
            stages_json=stages_up_to_done_json(get_template_stages(template), FINISHED),
        )
    )
    db_session.commit()
    _builtin_run(db_session, ticket, FINISHED, finished_at=utcnow() - timedelta(minutes=30))
    return ticket


def _builtin_run(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    *,
    finished_at: datetime,
    driver: OrchestrationDriver = OrchestrationDriver.BUILTIN_AUTOPILOT,
    worktree_id: str | None = None,
    asked_by_run_id: str = "",
) -> AgentRun:
    orch = OrchestrationRun(
        run_code=f"orch_{stage_key}_{finished_at.timestamp()}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        driver=driver,
        status=OrchestrationRunStatus.FAILED,
    )
    session.add(orch)
    session.commit()
    run = AgentRun(
        run_code=f"run_{stage_key}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        orchestration_run_id=orch.id,
        agent_id="backend_implementer",
        stage_key=stage_key,
        status=RunStatus.SUCCEEDED,
        created_at=finished_at - timedelta(minutes=1),
        finished_at=finished_at,
        worktree_id=worktree_id,
        asked_by_run_id=asked_by_run_id,
    )
    session.add(run)
    session.commit()
    return run


def _gate_event(
    session: Session, ticket: Ticket, stage_key: str, outcome: GateOutcome, at: datetime
) -> None:
    session.add(
        DomainEvent(
            type=EventType.GATE_EVALUATED,
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            payload_json=json.dumps({"outcome": outcome.value, "from_stage": stage_key}),
            created_at=at,
        )
    )
    session.commit()


def _profile(*commands: str) -> OrchestrationProfile:
    return OrchestrationProfile(
        slug="owed-gate",
        gates=GatesConfig(enabled=True, commands=list(commands), autofix_agent_fallback=False),
    )


def _drive(session: Session, ticket: Ticket, *commands: str) -> None:
    BuiltinOrchestrator(session).execute(ticket, _profile(*commands), max_stages=1)
    session.refresh(ticket)


def _gates(session: Session, ticket: Ticket) -> list[tuple[str, str]]:
    events = session.exec(
        select(DomainEvent)
        .where(DomainEvent.ticket_id == ticket.id, DomainEvent.type == EventType.GATE_EVALUATED)
        .order_by(DomainEvent.created_at)
    ).all()
    payloads = [json.loads(event.payload_json) for event in events]
    return [(p["from_stage"], p["outcome"]) for p in payloads]


def _dispatched(session: Session, ticket: Ticket) -> list[str]:
    runs = session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id).order_by(AgentRun.created_at)
    ).all()
    return [run.stage_key for run in runs][1:]


def _statuses(session: Session, ticket: Ticket) -> dict[str, StageStatus]:
    instance, stages = OrchestrationService(session)._resolve_stages(ticket)
    return parse_stage_map(instance, stages)


def test_a_stage_that_finished_with_no_driver_is_gated_before_the_next_one(
    db_session: Session, finished: Ticket
):
    _drive(db_session, finished, "true")

    assert _gates(db_session, finished)[0] == (FINISHED, "passed")
    assert _dispatched(db_session, finished) == [NEXT]


def test_a_failing_owed_gate_stops_the_next_stage(db_session: Session, finished: Ticket):
    _drive(db_session, finished, "false")

    assert _gates(db_session, finished) == [(FINISHED, "failed")]
    assert _dispatched(db_session, finished) == []
    assert _statuses(db_session, finished)[FINISHED] is StageStatus.BLOCKED


def test_a_failing_owed_gate_goes_back_to_the_stage_that_owes_it(
    db_session: Session, finished: Ticket
):
    """With agent passes on, the finished stage re-runs rather than the next one."""
    profile = _profile("false")
    profile.gates.autofix_agent_fallback = True
    BuiltinOrchestrator(db_session).execute(finished, profile, max_stages=1)

    assert _dispatched(db_session, finished) == [FINISHED]


@pytest.mark.parametrize("outcome", [GateOutcome.PASSED, GateOutcome.SKIPPED, GateOutcome.DISABLED])
def test_a_stage_already_gated_is_not_gated_again(db_session, finished, outcome):
    _gate_event(db_session, finished, FINISHED, outcome, utcnow() - timedelta(minutes=20))

    _drive(db_session, finished, "true")

    assert [g for g in _gates(db_session, finished) if g[0] == FINISHED] == [
        (FINISHED, outcome.value)
    ]


def test_a_gate_from_before_the_run_does_not_count(db_session: Session, finished: Ticket):
    """The stage was gated, sent back, and finished again with no driver."""
    _gate_event(
        db_session, finished, FINISHED, GateOutcome.PASSED, utcnow() - timedelta(minutes=60)
    )

    _drive(db_session, finished, "true")

    assert _gates(db_session, finished).count((FINISHED, "passed")) == 2


def test_another_stages_gate_does_not_count(db_session: Session, finished: Ticket):
    _gate_event(db_session, finished, "spec", GateOutcome.PASSED, utcnow() - timedelta(minutes=20))

    _drive(db_session, finished, "true")

    assert (FINISHED, "passed") in _gates(db_session, finished)


def test_a_failing_gate_the_driver_died_in_is_run_again(db_session: Session, finished: Ticket):
    """Recorded, then killed before it was acted on: no reroute, no block."""
    _gate_event(
        db_session, finished, FINISHED, GateOutcome.FAILED, utcnow() - timedelta(minutes=20)
    )

    _drive(db_session, finished, "true")

    assert _gates(db_session, finished)[:2] == [(FINISHED, "failed"), (FINISHED, "passed")]


def test_a_failing_gate_a_person_moved_past_is_not_run_again(db_session: Session, finished: Ticket):
    """The gate failed and blocked, and the stage was set done by hand: an answer."""
    _drive(db_session, finished, "false")
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(finished)
    finished.blocking_issues = ""
    set_stage_status(finished, instance, stages, FINISHED, StageStatus.DONE)
    finished.state = TicketState.IN_PROGRESS
    db_session.add(finished)
    db_session.add(instance)
    db_session.commit()

    assert owed_gate(db_session, finished, stages, parse_stage_map(instance, stages)) is None


@pytest.mark.parametrize(
    "fields",
    [
        {"driver": OrchestrationDriver.EXTERNAL_MCP},
        {"asked_by_run_id": "run-that-asked"},
    ],
    ids=["external-driver", "ask"],
)
def test_a_run_that_finishes_no_builtin_stage_owes_nothing(db_session, finished, fields):
    """Neither ends a stage of this driver's, so an earlier stage is not re-judged."""
    _gate_event(
        db_session, finished, FINISHED, GateOutcome.PASSED, utcnow() - timedelta(minutes=20)
    )
    _builtin_run(
        db_session, finished, "spec", finished_at=utcnow() - timedelta(minutes=10), **fields
    )

    _drive(db_session, finished, "true")

    assert "spec" not in [stage for stage, _ in _gates(db_session, finished)]


@pytest.mark.parametrize("status", [StageStatus.PENDING, StageStatus.AWAITING, StageStatus.WONT_DO])
def test_a_stage_that_is_not_done_owes_nothing(db_session: Session, finished: Ticket, status):
    """Sent back, waiting on a person, or skipped: there is no finished work to pass on."""
    instance, stages = OrchestrationService(db_session)._resolve_stages(finished)
    set_stage_status(finished, instance, stages, FINISHED, status)

    assert owed_gate(db_session, finished, stages, parse_stage_map(instance, stages)) is None


def test_a_signed_off_stage_is_gated_before_the_next_one(db_session: Session, finished: Ticket):
    """A person's approval marks the stage done outside any driver's pass."""
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(finished)
    set_stage_status(finished, instance, stages, FINISHED, StageStatus.AWAITING)
    approval = Approval(
        ticket_id=finished.id,
        workspace_id=finished.workspace_id,
        title="Sign off",
        stage_key=FINISHED,
        kind=ApprovalKind.WORKFLOW_GATE,
        status=ApprovalStatus.PENDING,
        tool_input_json=operator_judgment_gate_payload(FINISHED),
    )
    db_session.add_all([finished, instance, approval])
    db_session.commit()
    with mock.patch("loregarden.services.orchestration_resume.schedule_orchestration"):
        ApprovalService(db_session).resolve(approval.id, approved=True)
    db_session.refresh(finished)
    assert _statuses(db_session, finished)[FINISHED] is StageStatus.DONE

    _drive(db_session, finished, "true")

    assert _gates(db_session, finished)[0] == (FINISHED, "passed")


def test_a_stage_whose_worktree_is_gone_is_not_gated_on_the_shared_checkout(
    db_session: Session, finished: Ticket
):
    run = db_session.exec(select(AgentRun).where(AgentRun.ticket_id == finished.id)).one()
    run.worktree_id = "worktree-since-removed"
    db_session.add(run)
    db_session.commit()
    graded: list[str] = []

    def spy(*args, from_stage: str, **kwargs) -> GateRunResult:
        graded.append(from_stage)
        return GateRunResult(ok=True, outcome=GateOutcome.PASSED)

    with mock.patch("loregarden.services.gate_recovery.run_transition_gates", spy):
        _drive(db_session, finished, "true")

    assert graded == [NEXT]
    assert _gates(db_session, finished)[0] == (FINISHED, "skipped")


def test_the_invariant_reports_a_stage_dispatched_past_an_ungated_one(
    db_session: Session, finished: Ticket
):
    assert stages_left_ungated(db_session) == []

    later = _builtin_run(db_session, finished, NEXT, finished_at=utcnow())
    [violation] = stages_left_ungated(db_session)
    assert violation.entered_at == later.created_at

    _gate_event(db_session, finished, NEXT, GateOutcome.PASSED, utcnow())
    assert stages_left_ungated(db_session) == []


def test_the_invariant_ignores_a_stage_sent_back(db_session: Session, finished: Ticket):
    """A rework run of an earlier stage is not a dispatch past this one."""
    _builtin_run(db_session, finished, "spec", finished_at=utcnow())

    assert stages_left_ungated(db_session) == []
