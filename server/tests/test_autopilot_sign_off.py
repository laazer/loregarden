"""The initiative autopilot approves legacy stage sign-offs on work it started.

`Break Tests` on studio-loregarden-tdd-v3 carries a `legacy-stage-sign-off`
gate. Under an initiative autopilot that gate stopped every ticket for a person
until the autopilot learned to sign it. It signs only that gate, only on
tickets it dispatched, and only while it is on.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    AutopilotAction,
    AutopilotEvent,
    InitiativePlan,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.subtree_auto_run import resolve_gate_if_permitted
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select
from tests.history_helpers import decision_kinds


def _judgment(key: str, label: str) -> dict:
    return {
        "key": key,
        "label": label,
        "requirement": {"kind": "operator_judgment", "decision_prompt": f"{label}?"},
    }


BREAK_TESTS = WorkflowStageDef.model_validate(
    {
        "key": "test-break",
        "name": "Break Tests",
        "order": 1,
        "agent_id": "test_breaker",
        "exit_actions_enabled": True,
        "exit_actions": [_judgment("legacy-stage-sign-off", "Approve Break Tests completion")],
    }
)
#: An operator-judgment gate authored as a real question, not a migrated flag.
RELEASE_CALL = WorkflowStageDef.model_validate(
    {
        "key": "release-call",
        "name": "Release call",
        "order": 2,
        "agent_id": "test_breaker",
        "exit_actions_enabled": True,
        "exit_actions": [_judgment("ship-to-users", "Ship this to users")],
    }
)
STAGES = [
    BREAK_TESTS,
    RELEASE_CALL,
    WorkflowStageDef(key="done", name="Done", order=3, terminal=True),
]

_PASS_REPORT = (
    "done\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    + json.dumps({"status": "pass", "confidence": 0.9})
    + "\n<<<END_STAGE_REPORT>>>\n"
)


@pytest.fixture(name="world")
def world_fixture(db_session: Session, tmp_path):
    """A ticket on an initiative's plan, its workflow, and a running orchestration."""
    workspace = Workspace(slug=f"aso-{uuid4()}", name="ASO", repo_path=str(tmp_path))
    template = WorkflowTemplate(
        slug=f"aso-tpl-{uuid4()}",
        name="ASO",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json="[]",
    )
    db_session.add_all([workspace, template])
    db_session.commit()
    initiative = Ticket(
        external_id=f"aso-init-{uuid4()}",
        title="Ship it",
        work_item_type=WorkItemType.INITIATIVE,
    )
    ticket = Ticket(
        external_id=f"aso-{uuid4()}",
        workspace_id=workspace.id,
        title="Break it",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=BREAK_TESTS.key,
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add_all([initiative, ticket])
    db_session.commit()
    plan = InitiativePlan(initiative_id=initiative.id, autopilot=True)
    orch = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        approve_design_plans=True,
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add_all(
        [
            plan,
            orch,
            WorkflowInstance(
                ticket_id=ticket.id,
                template_id=template.id,
                current_stage_key=BREAK_TESTS.key,
                stages_json=initial_stages_json(STAGES),
            ),
        ]
    )
    db_session.commit()
    return ticket, plan, orch


def _dispatched_by_autopilot(session: Session, ticket: Ticket, plan: InitiativePlan) -> None:
    session.add(
        AutopilotEvent(
            initiative_id=plan.initiative_id, action=AutopilotAction.DISPATCHED, ticket_id=ticket.id
        )
    )
    session.commit()


def _finish_stage(session: Session, ticket: Ticket, orch: OrchestrationRun, stage) -> Approval:
    """Run `stage` to a clean pass and return the gate run_completion raised."""
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=stage.agent_id,
        stage_key=stage.key,
        status=RunStatus.RUNNING,
        orchestration_run_id=orch.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    OrchestrationService(session).complete_run(run, status=RunStatus.SUCCEEDED, stdout=_PASS_REPORT)
    return session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.stage_key == stage.key,
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
        )
    ).one()


def _signed_off_events(session: Session, ticket: Ticket) -> list[AutopilotEvent]:
    return list(
        session.exec(
            select(AutopilotEvent).where(
                AutopilotEvent.ticket_id == ticket.id,
                AutopilotEvent.action == AutopilotAction.SIGNED_OFF,
            )
        ).all()
    )


def test_autopilot_signs_off_break_tests_on_work_it_started(db_session, world):
    ticket, plan, orch = world
    _dispatched_by_autopilot(db_session, ticket, plan)

    gate = _finish_stage(db_session, ticket, orch, BREAK_TESTS)

    assert gate.status is ApprovalStatus.APPROVED
    assert gate.resolved_by == "autopilot"
    assert decision_kinds(db_session, ticket.id) == ["approved_legacy_sign_off"]
    assert [e.initiative_id for e in _signed_off_events(db_session, ticket)] == [plan.initiative_id]


def test_turned_off_the_gate_waits_for_a_person(db_session, world):
    ticket, plan, orch = world
    _dispatched_by_autopilot(db_session, ticket, plan)
    plan.autopilot = False
    db_session.add(plan)
    db_session.commit()

    gate = _finish_stage(db_session, ticket, orch, BREAK_TESTS)

    assert gate.status is ApprovalStatus.PENDING
    assert decision_kinds(db_session, ticket.id) == []
    assert _signed_off_events(db_session, ticket) == []


def test_work_the_autopilot_did_not_start_waits_for_a_person(db_session, world):
    ticket, _, orch = world

    gate = _finish_stage(db_session, ticket, orch, BREAK_TESTS)

    assert gate.status is ApprovalStatus.PENDING
    assert decision_kinds(db_session, ticket.id) == []


def test_an_authored_judgment_gate_stays_a_persons(db_session, world):
    """AC-6: only the migrated legacy sign-off is the autopilot's to answer."""
    ticket, plan, orch = world
    _dispatched_by_autopilot(db_session, ticket, plan)

    gate = _finish_stage(db_session, ticket, orch, RELEASE_CALL)

    assert gate.status is ApprovalStatus.PENDING
    assert _signed_off_events(db_session, ticket) == []


def test_the_orchestrator_loop_signs_off_a_parked_gate(db_session, world):
    """A gate already waiting when the loop reaches it — e.g. raised before the
    autopilot was turned back on — is signed off there too."""
    ticket, plan, orch = world
    plan.autopilot = False
    db_session.add(plan)
    db_session.commit()
    _dispatched_by_autopilot(db_session, ticket, plan)
    gate = _finish_stage(db_session, ticket, orch, BREAK_TESTS)
    assert gate.status is ApprovalStatus.PENDING

    plan.autopilot = True
    db_session.add(plan)
    db_session.commit()

    assert resolve_gate_if_permitted(db_session, ticket, orch, BREAK_TESTS, auto_approve=True)
    db_session.refresh(gate)
    assert gate.status is ApprovalStatus.APPROVED
    assert decision_kinds(db_session, ticket.id) == ["approved_legacy_sign_off"]
    assert len(_signed_off_events(db_session, ticket)) == 1


def test_a_gate_on_a_child_of_work_it_started_is_signed(db_session, world):
    """It queues the parent; the parent's orchestration runs the child, whose gate opens."""
    ticket, plan, orch = world
    parent = Ticket(
        external_id=f"aso-parent-{uuid4()}",
        workspace_id=ticket.workspace_id,
        title="The capability it queued",
        work_item_type=WorkItemType.CAPABILITY,
        state=TicketState.IN_PROGRESS,
    )
    db_session.add(parent)
    db_session.commit()
    ticket.parent_ticket_id = parent.id
    db_session.add(ticket)
    db_session.commit()
    _dispatched_by_autopilot(db_session, parent, plan)

    gate = _finish_stage(db_session, ticket, orch, BREAK_TESTS)

    assert gate.status is ApprovalStatus.APPROVED
    assert decision_kinds(db_session, ticket.id) == ["approved_legacy_sign_off"]


def _auto_approve(session: Session, orch: OrchestrationRun) -> None:
    orch.auto_approve = True
    session.add(orch)
    session.commit()


def test_an_auto_approve_run_signs_off_break_tests_without_the_autopilot(db_session, world):
    ticket, _, orch = world
    _auto_approve(db_session, orch)

    gate = _finish_stage(db_session, ticket, orch, BREAK_TESTS)

    assert gate.status is ApprovalStatus.APPROVED
    assert gate.resolved_by == "automation"
    assert gate.resolving_orchestration_run_id == orch.id
    assert decision_kinds(db_session, ticket.id) == ["approved_legacy_sign_off"]
    assert _signed_off_events(db_session, ticket) == [], "the autopilot did not sign this"


def test_an_auto_approve_run_leaves_an_authored_judgment_gate_to_a_person(db_session, world):
    """AC-6 still holds: auto-approve answers the legacy sign-off and nothing else."""
    ticket, _, orch = world
    _auto_approve(db_session, orch)

    gate = _finish_stage(db_session, ticket, orch, RELEASE_CALL)

    assert gate.status is ApprovalStatus.PENDING
    assert decision_kinds(db_session, ticket.id) == []


def test_an_auto_approve_loop_signs_off_a_gate_parked_before_it(db_session, world):
    ticket, _, orch = world
    gate = _finish_stage(db_session, ticket, orch, BREAK_TESTS)
    assert gate.status is ApprovalStatus.PENDING
    _auto_approve(db_session, orch)

    assert resolve_gate_if_permitted(db_session, ticket, orch, BREAK_TESTS, auto_approve=True)
    db_session.refresh(gate)
    assert gate.status is ApprovalStatus.APPROVED
    assert decision_kinds(db_session, ticket.id) == ["approved_legacy_sign_off"]
