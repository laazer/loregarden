"""`agent_group` survives every place a stage definition is stored, and old rows load without one.

Spec lg-run-economics-2636 §1 and §4. AC1: the field round-trips through the
Studio workflow API and template version snapshots, and templates saved before
this change load with no group. The ticket projection shows each step's group
and marks the step at which the group's gates run. The `agent_runs` columns
that record a group run arrive by an idempotent migration.
"""

from __future__ import annotations

import json

import pytest
from loregarden.core.workflow_loader import get_template_stages, get_template_stages_at_version
from loregarden.db import versions
from loregarden.db.migration_registry import import_submodules
from loregarden.db.migration_utils import table_columns
from loregarden.models.domain import (
    StudioWorkflow,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    StudioWorkflowUpdate,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkflowTemplateVersion,
    WorkItemType,
    Workspace,
)
from loregarden.services.studio_service import StudioService
from loregarden.services.workflow_state import (
    build_stage_views,
    initial_stages_json,
    stage_views_readonly,
)
from sqlalchemy import create_engine, text
from sqlmodel import Session, col, select

MIGRATION_ID = "20261116_agent_run_groups"
RUN_COLUMNS = {"agent_group_key", "covered_stage_keys_json"}


def _group_of(stages, key: str):
    stage = next(stage for stage in stages if stage.key == key)
    return getattr(stage, "agent_group", None)


@pytest.fixture
def svc(db_session: Session) -> StudioService:
    return StudioService(db_session)


@pytest.fixture
def grouped_draft(svc: StudioService) -> str:
    svc.create_workflow(
        StudioWorkflowCreate(
            slug="grouped",
            name="Grouped",
            stages=[
                StudioWorkflowStage(
                    key="alpha", name="Alpha", order=1, agent_id="planner", agent_group="g"
                ),
                StudioWorkflowStage(key="done", name="Done", order=2, terminal=True),
            ],
        )
    )
    return "grouped"


def _template(session: Session, slug: str) -> WorkflowTemplate:
    return session.exec(select(WorkflowTemplate).where(WorkflowTemplate.slug == slug)).one()


# ---- the Studio workflow API ----------------------------------------------------------------


def test_create_stores_the_group_and_reads_it_back(svc, grouped_draft):
    assert _group_of(svc.get_workflow(grouped_draft).stages, "alpha") == "g"


def test_update_changes_the_group(svc, grouped_draft):
    current = svc.get_workflow(grouped_draft).stages
    edited = [
        stage.model_copy(update={"agent_group": "h"}) if stage.key == "alpha" else stage
        for stage in current
    ]
    svc.update_workflow(grouped_draft, StudioWorkflowUpdate(stages=edited))
    assert _group_of(svc.get_workflow(grouped_draft).stages, "alpha") == "h"


def test_publish_carries_the_group_into_the_template_and_its_version_snapshot(
    svc, grouped_draft, db_session
):
    svc.publish_workflow(grouped_draft)
    template = _template(db_session, "studio-grouped")

    assert _group_of(get_template_stages(template), "alpha") == "g"
    snapshot = db_session.exec(
        select(WorkflowTemplateVersion)
        .where(WorkflowTemplateVersion.template_id == template.id)
        .order_by(col(WorkflowTemplateVersion.version).desc())
    ).first()
    stored = json.loads(json.loads(snapshot.snapshot_json)["stages_json"])
    assert next(item for item in stored if item["key"] == "alpha").get("agent_group") == "g"
    version_view = svc.get_workflow_version(grouped_draft, snapshot.version)
    assert _group_of(version_view.snapshot.stages, "alpha") == "g"


def test_a_pinned_version_keeps_the_group_it_was_published_with(svc, grouped_draft, db_session):
    svc.publish_workflow(grouped_draft)
    first_version = _template(db_session, "studio-grouped").version
    current = svc.get_workflow(grouped_draft).stages
    svc.update_workflow(
        grouped_draft,
        StudioWorkflowUpdate(
            stages=[
                stage.model_copy(update={"agent_group": ""}) if stage.key == "alpha" else stage
                for stage in current
            ]
        ),
    )
    svc.publish_workflow(grouped_draft)
    template = _template(db_session, "studio-grouped")

    pinned = get_template_stages_at_version(db_session, template, first_version)
    assert _group_of(pinned, "alpha") == "g"
    assert _group_of(get_template_stages(template), "alpha") == ""


# ---- rows written before the field existed ----------------------------------------------------


def test_a_template_stage_with_no_group_key_loads_as_ungrouped(db_session, svc):
    template = WorkflowTemplate(
        slug="legacy-template",
        name="Legacy",
        stages_json=json.dumps(
            [
                {"key": "work", "name": "Work", "order": 1, "agent_id": "planner"},
                {"key": "done", "name": "Done", "order": 2, "terminal": True},
            ]
        ),
    )
    db_session.add(template)
    db_session.commit()

    assert _group_of(get_template_stages(template), "work") == ""
    assert _group_of(svc.get_workflow("legacy-template").stages, "work") == ""


def test_a_draft_stage_stored_with_a_null_group_loads_as_ungrouped(db_session, svc, grouped_draft):
    draft = db_session.exec(
        select(StudioWorkflow).where(StudioWorkflow.slug == grouped_draft)
    ).one()
    stored = json.loads(draft.stages_json)
    for item in stored:
        item["agent_group"] = None
    draft.stages_json = json.dumps(stored)
    db_session.add(draft)
    db_session.commit()

    assert _group_of(svc.get_workflow(grouped_draft).stages, "alpha") == ""


# ---- the ticket projection (§4) ------------------------------------------------------------------


def _projection_stages() -> list[WorkflowStageDef]:
    return [
        WorkflowStageDef(key="a", name="A", order=1, agent_id="planner"),
        WorkflowStageDef(key="b", name="B", order=2, agent_id="spec", agent_group="g"),
        WorkflowStageDef(key="c", name="C", order=3, agent_id="test_designer", agent_group="g"),
        WorkflowStageDef(key="done", name="Done", order=4, terminal=True),
    ]


@pytest.fixture
def projected(db_session: Session) -> tuple[Ticket, WorkflowInstance, list[WorkflowStageDef]]:
    stages = _projection_stages()
    workspace = Workspace(slug="projection", name="Projection", repo_path="/nonexistent/projection")
    db_session.add(workspace)
    db_session.commit()
    template = WorkflowTemplate(
        slug="projection-tpl",
        name="Projection",
        stages_json=json.dumps([stage.model_dump(mode="json") for stage in stages]),
    )
    db_session.add(template)
    db_session.commit()
    ticket = Ticket(
        external_id="projection",
        workspace_id=workspace.id,
        title="Projection",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="a",
    )
    db_session.add(ticket)
    db_session.commit()
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="a",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()
    return ticket, instance, stages


@pytest.mark.parametrize("builder", [stage_views_readonly, build_stage_views])
def test_stage_views_show_each_steps_group_and_where_its_gates_run(projected, builder):
    ticket, instance, stages = projected
    views = {view.key: view for view in builder(ticket, instance, stages)}

    assert getattr(views["b"], "agent_group", None) == "g"
    assert getattr(views["c"], "agent_group", None) == "g"
    assert getattr(views["b"], "gates_at_group_end", None) is False
    assert getattr(views["c"], "gates_at_group_end", None) is True
    for key in ("a", "done"):
        assert getattr(views[key], "agent_group", None) == ""
        assert getattr(views[key], "gates_at_group_end", None) is False


# ---- the agent_runs migration ------------------------------------------------------------------


def _migration():
    import_submodules(versions)
    found = [m for m in versions.REGISTRY.registered() if m.id == MIGRATION_ID]
    assert found, f"migration {MIGRATION_ID} is not registered"
    return found[0]


def test_the_migration_follows_the_newest_id_on_main():
    assert _migration().after == "20261115_integration_branch_moves"


def test_the_live_schema_has_both_group_columns(isolated_db):
    with isolated_db.connect() as conn:
        assert RUN_COLUMNS <= table_columns(conn, "agent_runs")


def test_the_migration_adds_both_columns_and_is_idempotent(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE agent_runs (id VARCHAR PRIMARY KEY)"))
        conn.execute(text("INSERT INTO agent_runs (id) VALUES ('old-run')"))
        migration.migrate(conn)
        after_once = table_columns(conn, "agent_runs")
        migration.migrate(conn)
        assert table_columns(conn, "agent_runs") == after_once
        assert RUN_COLUMNS <= after_once
        row = conn.execute(
            text("SELECT agent_group_key, covered_stage_keys_json FROM agent_runs")
        ).one()
    assert tuple(row) == ("", "[]"), "an old run reads as ungrouped"
