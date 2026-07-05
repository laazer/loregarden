import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import Ticket, TicketState, WorkItemType, WorkflowInstance, WorkflowTemplate, Workspace
from loregarden.services.workflow_state import build_stage_views, initial_stages_json


def test_blobert_template_loaded(client: TestClient):
    res = client.get("/api/workflows/templates")
    assert res.status_code == 200
    templates = {item["slug"]: item for item in res.json()}
    assert "blobert-tdd" in templates
    blobert = templates["blobert-tdd"]
    assert blobert["name"] == "Blobert TDD"
    assert blobert["stage_count"] == 11


def test_blobert_template_stage_metadata(client: TestClient, db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None

    stages = get_template_stages(template)
    keys = [stage.key for stage in sorted(stages, key=lambda item: item.order)]
    assert keys == [
        "planning",
        "domain_consultation",
        "specification",
        "test_design",
        "test_break",
        "implementation",
        "script_review",
        "ac_gate",
        "playtest",
        "learning",
        "done",
    ]

    implementation = next(stage for stage in stages if stage.key == "implementation")
    assert implementation.stage_type == "classify"
    assert {route.agent_id for route in implementation.classify_routes} == {
        "core_simulation",
        "gameplay_systems",
        "presentation",
        "engine_integration",
        "implementation_frontend",
    }

    script_review = next(stage for stage in stages if stage.key == "script_review")
    assert script_review.stage_type == "parallel"
    assert [agent.agent_id for agent in script_review.parallel_agents] == [
        "gdscript_reviewer",
        "static_qa",
        "architecture_reviewer",
    ]

    optional = {stage.key for stage in stages if stage.optional}
    assert optional == {"domain_consultation", "playtest", "learning"}


def test_blobert_ticket_stage_views_for_stepper(client: TestClient, db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None

    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    ticket = Ticket(
        external_id="blobert-stepper-test",
        workspace_id=ws.id,
        title="Blobert stepper ticket",
        description="Verify stage view metadata",
        state=TicketState.BACKLOG,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="planning",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    stages = get_template_stages(template)
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="planning",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    res = client.get(f"/api/tickets/{ticket.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["workflow_template_slug"] == "blobert-tdd"

    by_key = {stage["key"]: stage for stage in body["stages"]}
    assert by_key["script_review"]["stage_type"] == "parallel"
    assert [agent["agent_id"] for agent in by_key["script_review"]["agents"]] == [
        "gdscript_reviewer",
        "static_qa",
        "architecture_reviewer",
    ]
    assert by_key["implementation"]["stage_type"] == "classify"
    assert len(by_key["implementation"]["agents"]) == 5
    assert by_key["playtest"]["stage_type"] == "agent"
    assert by_key["playtest"]["agent_id"] == ""

    views = build_stage_views(ticket, instance, stages)
    assert views[6].name == "Script Review"
    assert views[6].stage_type == "parallel"
