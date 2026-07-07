from fastapi.testclient import TestClient
from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select


def test_route_workflow_api_moves_cursor_upstream(client: TestClient, db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None

    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    ticket = Ticket(
        external_id="route-workflow-api-test",
        workspace_id=ws.id,
        title="Route workflow API",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="ac_gate",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    stages = get_template_stages(template)
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="ac_gate",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    res = client.post(
        f"/api/tickets/{ticket.id}/route-workflow",
        json={
            "from_stage_key": "ac_gate",
            "outcome": "reject",
            "next_stage_key": "implementation",
            "next_agent": "core_simulation",
            "blocking_issues": "Needs more tests",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["workflow_stage_key"] == "implementation"
    assert body["next_agent"] == "core_simulation"
    assert "Needs more tests" in body["blocking_issues"]

    db_session.refresh(instance)
    stage_map = parse_stage_map(instance, stages)
    assert stage_map["implementation"] == StageStatus.PENDING
    assert stage_map["ac_gate"] == StageStatus.PENDING

    transitions = StateMachine.parse_transitions(template.transitions_json)
    reject = StateMachine.resolve_transition_target(transitions, "ac_gate", "reject")
    assert reject is not None
    assert reject[0] == "implementation"
