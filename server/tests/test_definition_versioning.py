"""Agents and workflow templates are DB-authoritative and versioned."""

import json

import pytest
from fastapi.testclient import TestClient
from loregarden.agents.registry import AGENTS, get_agent
from loregarden.core.workflow_loader import (
    get_template_stages,
    get_template_stages_at_version,
    sync_workflow_templates,
)
from loregarden.models.domain import (
    StudioAgent,
    StudioAgentUpdate,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    StudioWorkflowUpdate,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.studio_service import StudioService, seed_builtin_agents
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select

# ---- Agent seeding + versioning -------------------------------------------------


def test_seed_builtin_agents_is_idempotent(db_session: Session):
    # db_session is already seeded via the client fixture.
    before = len(db_session.exec(select(StudioAgent)).all())
    again = seed_builtin_agents(db_session)
    after = len(db_session.exec(select(StudioAgent)).all())
    assert again == []  # nothing new to seed
    assert after == before
    # Every registry built-in resolved into the DB.
    slugs = {a.slug for a in db_session.exec(select(StudioAgent)).all()}
    assert set(AGENTS) <= slugs


def test_seed_wires_registry_model_pin(db_session: Session):
    triage = db_session.exec(select(StudioAgent).where(StudioAgent.slug == "triage")).first()
    assert triage is not None
    assert triage.default_model == "haiku"  # registry claude_model, now effective
    assert triage.built_in is True
    assert triage.version == 1


def test_agent_edit_bumps_version_and_appends_history(db_session: Session):
    svc = StudioService(db_session)
    svc.update_agent("learning", StudioAgentUpdate(description="v2 desc", change_note="tweak"))
    agent = db_session.exec(select(StudioAgent).where(StudioAgent.slug == "learning")).first()
    assert agent.version == 2
    history = svc.list_agent_versions("learning")
    assert [v.version for v in history] == [2, 1]
    assert history[0].change_note == "tweak"
    assert history[-1].created_by == "seed"


def test_agent_restore_creates_new_head_version(db_session: Session):
    svc = StudioService(db_session)
    original = svc.get_agent("learning").description
    svc.update_agent("learning", StudioAgentUpdate(description="changed"))
    svc.restore_agent_version("learning", 1)
    agent = db_session.exec(select(StudioAgent).where(StudioAgent.slug == "learning")).first()
    assert agent.version == 3  # v1 seed, v2 edit, v3 restore
    assert agent.description == original  # content of v1 restored


def test_get_agent_resolves_builtin_from_db(db_session: Session):
    cfg = get_agent("planner")
    assert cfg is not None
    assert cfg.get("studio") is True  # came from studio_agents, not the registry dict
    assert cfg.get("role_body")


# ---- Workflow seeding + versioning + validation ---------------------------------


def test_sync_workflow_templates_does_not_clobber_edits(db_session: Session):
    tpl = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "loregarden-tdd")
    ).first()
    assert tpl is not None
    tpl.description = "hand edited"
    db_session.add(tpl)
    db_session.commit()
    # Re-sync must be seed-when-missing: the existing slug is left untouched.
    seeded = sync_workflow_templates(db_session)
    assert all(t.slug != "loregarden-tdd" for t in seeded)
    db_session.refresh(tpl)
    assert tpl.description == "hand edited"


def test_publish_versions_the_template(db_session: Session):
    svc = StudioService(db_session)
    svc.create_workflow(
        StudioWorkflowCreate(
            slug="verwf",
            name="Ver WF",
            stages=[
                StudioWorkflowStage(key="planning", name="Plan", agent_id="planner", order=1),
                StudioWorkflowStage(key="done", name="Done", agent_id="", order=2),
            ],
        )
    )
    svc.publish_workflow("verwf")
    assert [v.version for v in svc.list_workflow_versions("verwf")] == [1]
    svc.publish_workflow("verwf")
    assert [v.version for v in svc.list_workflow_versions("verwf")] == [2, 1]


def test_workflow_rejects_unknown_agent(db_session: Session):
    svc = StudioService(db_session)
    with pytest.raises(ValueError, match="unknown agent"):
        svc.create_workflow(
            StudioWorkflowCreate(
                slug="badwf",
                name="Bad WF",
                stages=[
                    StudioWorkflowStage(key="s1", name="S1", agent_id="does_not_exist", order=1),
                ],
            )
        )


def test_template_stages_resolve_from_pinned_version(db_session: Session):
    """Editing a template does not change what a pinned (older) version resolves to."""
    svc = StudioService(db_session)
    svc.create_workflow(
        StudioWorkflowCreate(
            slug="pinwf",
            name="Pin WF",
            stages=[StudioWorkflowStage(key="planning", name="Plan", agent_id="planner", order=1)],
        )
    )
    svc.publish_workflow("pinwf")  # template v1
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-pinwf")
    ).first()
    v1_stage_count = len(json.loads(template.stages_json))

    # Add a stage and re-publish -> template v2 with more stages.
    svc.update_workflow(
        "pinwf",
        StudioWorkflowUpdate(
            stages=[
                StudioWorkflowStage(key="planning", name="Plan", agent_id="planner", order=1),
                StudioWorkflowStage(key="spec", name="Spec", agent_id="spec", order=2),
            ]
        ),
    )
    svc.publish_workflow("pinwf")
    db_session.refresh(template)
    assert template.version == 2

    # A ticket pinned to v1 still sees v1's stage set, not the edited v2.
    pinned = get_template_stages_at_version(db_session, template, 1)
    assert len(pinned) == v1_stage_count
    live = get_template_stages_at_version(db_session, template, template.version)
    assert len(live) == 2


# ---- Per-run pinning ------------------------------------------------------------


def test_run_pins_agent_version(db_session: Session):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    tpl = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "loregarden-tdd")
    ).first()
    stages = get_template_stages(tpl)
    first = min(stages, key=lambda s: s.order)
    ticket = Ticket(
        external_id="ver-pin-run",
        workspace_id=ws.id,
        title="pin run",
        state=TicketState.BACKLOG,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=first.key,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=tpl.id,
            template_version=tpl.version,
            current_stage_key=first.key,
            stages_json=initial_stages_json(stages),
        )
    )
    db_session.commit()

    run = OrchestrationService(db_session).start_run(ticket, stage_key=first.key)
    assert run.agent_version is not None
    agent = db_session.exec(select(StudioAgent).where(StudioAgent.slug == run.agent_id)).first()
    assert run.agent_version == agent.version


# ---- REST endpoints -------------------------------------------------------------


def test_agent_version_rest_endpoints(client: TestClient):
    edit = client.patch(
        "/api/studio/agents/planner",
        json={"description": "api edit", "change_note": "via api"},
    )
    assert edit.status_code == 200
    assert edit.json()["version"] == 2

    versions = client.get("/api/studio/agents/planner/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["change_note"] == "via api"

    detail = client.get("/api/studio/agents/planner/versions/1").json()
    assert detail["snapshot"]["role_body"]

    restored = client.post("/api/studio/agents/planner/versions/1/restore")
    assert restored.status_code == 200
    assert restored.json()["version"] == 3
