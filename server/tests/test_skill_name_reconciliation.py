import json

from loregarden.config import settings
from loregarden.core.workflow_loader import sync_workflow_templates
from loregarden.db.migrations import apply_migrations
from loregarden.models.domain import WorkflowTemplate, WorkflowTemplateVersion
from loregarden.skills.registry import get_skill, skill_search_dirs
from sqlalchemy import text
from sqlmodel import Session, select

PHANTOM_SKILLS = {
    "verify",
    "consult",
    "spec",
    "test_design",
    "test_break",
    "apply_patch",
    "static_qa",
    "index_repo",
    "run_tests",
    "ac_gate",
    "learning",
    "review",
}


def _declared_skill_names(stage: dict):
    if stage.get("skill_name"):
        yield stage["skill_name"]
    for slot in ("agents", "parallel_agents", "classify_routes"):
        for item in stage.get(slot) or []:
            if item.get("skill_name"):
                yield item["skill_name"]


def test_every_seeded_template_skill_resolves_under_chain(db_session: Session):
    sync_workflow_templates(db_session)

    for template in db_session.exec(select(WorkflowTemplate)).all():
        for stage in json.loads(template.stages_json or "[]"):
            for skill_name in _declared_skill_names(stage):
                assert get_skill(skill_name, agent_context_dir=settings.agent_context_dir), (
                    template.slug,
                    stage.get("key"),
                    skill_name,
                    skill_search_dirs(settings.agent_context_dir),
                )


def test_phantom_skills_are_cleared_from_templates_and_studio_workflows(isolated_db):
    apply_migrations(isolated_db)

    with isolated_db.connect() as conn:
        for table in ("workflow_templates", "studio_workflows"):
            if not conn.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                continue
            rows = conn.execute(text(f"SELECT slug, stages_json FROM {table}")).mappings().all()
            for row in rows:
                for stage in json.loads(row["stages_json"] or "[]"):
                    declared = set(_declared_skill_names(stage))
                    assert not (declared & PHANTOM_SKILLS), (table, row["slug"], stage.get("key"))


def test_skill_reconciliation_migration_preserves_resolvable_v3_names(db_session: Session):
    apply_migrations(db_session.bind)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-loregarden-tdd-v3")
    ).one()
    stages = {stage["key"]: stage for stage in json.loads(template.stages_json or "[]")}

    plan_stage = stages["plan"]
    assert {item["skill_name"] for item in plan_stage["parallel_agents"]} >= {
        "plan-risk",
        "plan-seams",
        "plan-simplest",
    }
    assert stages["plan-synthesis"]["skill_name"] == "plan-synthesis"
    assert stages["ui-design"]["skill_name"] == "plan"
    implement_route_skills = {
        route["skill_name"]
        for route in stages["implement"]["classify_routes"]
        if route.get("skill_name")
    }
    assert "refactor" in implement_route_skills


def test_skill_reconciliation_migration_is_idempotent_and_snapshots(db_session: Session):
    apply_migrations(db_session.bind)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-loregarden-tdd-v3")
    ).one()
    version_after_first = template.version
    migration_snapshots = db_session.exec(
        select(WorkflowTemplateVersion).where(
            WorkflowTemplateVersion.template_id == template.id,
            WorkflowTemplateVersion.created_by == "migration",
        )
    ).all()
    assert migration_snapshots

    apply_migrations(db_session.bind)
    db_session.refresh(template)

    assert template.version == version_after_first
