import json

from fastapi.testclient import TestClient
from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_loader import sync_workflow_templates
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select


def _blobert_transitions() -> list[dict[str, str]]:
    return [
        {"from": "script_review", "to": "ac_gate", "when": "pass"},
        {"from": "script_review", "to": "implementation", "when": "reject"},
        {"from": "ac_gate", "to": "playtest", "when": "pass"},
        {"from": "ac_gate", "to": "implementation", "when": "reject"},
        {"from": "test_design", "to": "specification", "when": "reject"},
    ]


def test_resolve_transition_target_reject_route():
    transitions = _blobert_transitions()
    routed = StateMachine.resolve_transition_target(transitions, "ac_gate", "reject")
    assert routed == ("implementation", "")


def test_resolve_transition_target_pass_route():
    transitions = _blobert_transitions()
    routed = StateMachine.resolve_transition_target(transitions, "ac_gate", "pass")
    assert routed == ("playtest", "")


def test_resolve_transition_target_legacy_linear():
    transitions = [{"from": "planning", "to": "specification"}]
    routed = StateMachine.resolve_transition_target(transitions, "planning", "pass")
    assert routed == ("specification", "")


def test_reset_upstream_stages_reopens_rework_window():
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="implementation", name="Implementation", order=6),
        WorkflowStageDef(key="script_review", name="Script Review", order=7),
        WorkflowStageDef(key="ac_gate", name="AC Gate", order=8),
    ]
    stage_map = {
        "implementation": StageStatus.DONE,
        "script_review": StageStatus.DONE,
        "ac_gate": StageStatus.RUNNING,
    }
    reset = StateMachine.reset_upstream_stages(
        stage_map,
        stages,
        from_key="ac_gate",
        to_key="implementation",
    )
    assert reset["implementation"] == StageStatus.PENDING
    assert reset["script_review"] == StageStatus.PENDING
    assert reset["ac_gate"] == StageStatus.PENDING


def test_complete_stage_routes_upstream_via_api(client: TestClient, db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None

    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    ticket = Ticket(
        external_id="upstream-route-test",
        workspace_id=ws.id,
        title="Upstream route test",
        description="Verify reject routing",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="ac_gate",
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="ac_gatekeeper",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    stages = json.loads(template.stages_json)
    from loregarden.core.workflow_loader import get_template_stages

    stage_defs = get_template_stages(template)
    stage_map = {item["key"]: StageStatus.DONE for item in stages if item["key"] != "ac_gate"}
    stage_map["ac_gate"] = StageStatus.RUNNING
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="ac_gate",
        stages_json=json.dumps(
            [{"key": key, "status": status.value} for key, status in stage_map.items()]
        ),
    )
    db_session.add(instance)
    db_session.commit()

    started = client.post(
        f"/api/orchestration/tickets/{ticket.id}/start",
        json={"driver": "external_mcp"},
    )
    assert started.status_code == 200
    run_id = started.json()["id"]

    complete = client.post(
        f"/api/orchestration/runs/{run_id}/complete_stage",
        json={
            "stage_key": "ac_gate",
            "outcome": "reject",
            "next_stage_key": "implementation",
            "next_agent": "core_simulation",
            "blocking_issues": "AC-2 missing test evidence",
        },
    )
    assert complete.status_code == 200
    body = complete.json()
    assert body["workflow_stage_key"] == "implementation"

    db_session.refresh(ticket)
    db_session.refresh(instance)
    refreshed_map = parse_stage_map(instance, stage_defs)
    assert refreshed_map["implementation"] == StageStatus.PENDING
    assert refreshed_map["script_review"] == StageStatus.PENDING
    assert refreshed_map["ac_gate"] == StageStatus.PENDING
    assert ticket.next_agent == "core_simulation"
    assert "AC-2" in ticket.blocking_issues


def test_apply_stage_route_uses_template_reject_transition(db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    from loregarden.core.workflow_loader import get_template_stages

    stages = get_template_stages(template)
    transitions = StateMachine.parse_transitions(template.transitions_json)

    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    ticket = Ticket(
        external_id="template-reject-route",
        workspace_id=ws.id,
        title="Template reject route",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="script_review",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    plan = apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key="script_review",
        outcome="reject",
    )
    assert plan.to_key == "implementation"
    assert plan.upstream is True
    assert ticket.workflow_stage_key == "implementation"

    reject = StateMachine.resolve_transition_target(transitions, "script_review", "reject")
    assert reject is not None
    assert reject[0] == "implementation"


def test_blobert_template_includes_reject_transitions(client: TestClient, db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    transitions = StateMachine.parse_transitions(template.transitions_json)
    reject_targets = {
        (item["from"], item["to"])
        for item in transitions
        if StateMachine._transition_when(item) == "reject"
    }
    assert ("script_review", "implementation") in reject_targets
    assert ("ac_gate", "implementation") in reject_targets
    assert ("test_design", "specification") in reject_targets
