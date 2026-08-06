import json
from uuid import uuid4

from loregarden.config import settings
from loregarden.core.workflow_loader import sync_workflow_templates
from loregarden.db.migrations import apply_migrations
from loregarden.models.domain import WorkflowTemplate, WorkflowTemplateVersion
from loregarden.skills.registry import get_skill, skill_search_dirs
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

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


def _v3_stages_with_phantoms_and_resolvable() -> list[dict]:
    return [
        {
            "key": "plan",
            "name": "Plan",
            "order": 1,
            "stage_type": "parallel",
            "agent_id": "planner",
            "skill_name": "",
            "parallel_agents": [
                {"agent_id": "planner", "skill_name": "plan-risk"},
                {"agent_id": "planner", "skill_name": "plan-seams"},
                {"agent_id": "planner", "skill_name": "plan-simplest"},
            ],
        },
        {
            "key": "plan-synthesis",
            "name": "Plan synthesis",
            "order": 2,
            "stage_type": "agent",
            "agent_id": "planner",
            "skill_name": "plan-synthesis",
        },
        {
            "key": "ui-design",
            "name": "UI Design",
            "order": 3,
            "stage_type": "agent",
            "agent_id": "planner",
            "skill_name": "plan",
        },
        {
            "key": "implement",
            "name": "Implement",
            "order": 4,
            "stage_type": "classify",
            "agent_id": "backend_implementer",
            "skill_name": "apply_patch",
            "classify_routes": [
                {
                    "agent_id": "backend_implementer",
                    "skill_name": "refactor",
                    "specialties": ["refactor"],
                },
                {
                    "agent_id": "backend_implementer",
                    "skill_name": "",
                    "specialties": ["backend"],
                    "default": True,
                },
            ],
        },
        {
            "key": "verify",
            "name": "Verify",
            "order": 5,
            "stage_type": "verify",
            "agent_id": "verifier",
            "skill_name": "verify",
        },
    ]


def _seed_v3_template(engine) -> str:
    template_id = str(uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, source_path, "
                "created_at, version, built_in) "
                "VALUES (:id, 'studio-loregarden-tdd-v3', 'v3', '', :st, '[]', "
                "'studio:loregarden-tdd-v3', :now, 1, 0)"
            ),
            {
                "id": template_id,
                "st": json.dumps(_v3_stages_with_phantoms_and_resolvable()),
                "now": "2026-01-01T00:00:00",
            },
        )
        conn.execute(
            text(
                "INSERT INTO studio_workflows "
                "(id, slug, name, description, stages_json, transitions_json, created_at, updated_at) "
                "VALUES (:id, 'phantom-draft', 'draft', '', :st, '[]', :now, :now)"
            ),
            {
                "id": str(uuid4()),
                "st": json.dumps(
                    [
                        {
                            "key": "work",
                            "name": "Work",
                            "order": 1,
                            "agent_id": "backend_implementer",
                            "skill_name": "consult",
                            "stage_type": "agent",
                            "classify_routes": [],
                            "parallel_agents": [],
                        }
                    ]
                ),
                "now": "2026-01-01T00:00:00",
            },
        )
    return template_id


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


def test_phantom_skills_are_cleared_from_templates_and_studio_workflows(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'phantoms.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    _seed_v3_template(engine)
    apply_migrations(engine)

    with engine.connect() as conn:
        for table in ("workflow_templates", "studio_workflows"):
            rows = conn.execute(text(f"SELECT slug, stages_json FROM {table}")).mappings().all()
            for row in rows:
                for stage in json.loads(row["stages_json"] or "[]"):
                    declared = set(_declared_skill_names(stage))
                    assert not (declared & PHANTOM_SKILLS), (table, row["slug"], stage.get("key"))


def test_skill_reconciliation_migration_preserves_resolvable_v3_names(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'preserve.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    _seed_v3_template(engine)
    apply_migrations(engine)

    with Session(engine) as session:
        template = session.exec(
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
    assert stages["verify"]["skill_name"] == ""
    assert stages["implement"]["skill_name"] == ""


def test_skill_reconciliation_migration_is_idempotent_and_snapshots(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'idempotent.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    _seed_v3_template(engine)
    apply_migrations(engine)

    with Session(engine) as session:
        template = session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-loregarden-tdd-v3")
        ).one()
        version_after_first = template.version
        migration_snapshots = session.exec(
            select(WorkflowTemplateVersion).where(
                WorkflowTemplateVersion.template_id == template.id,
                WorkflowTemplateVersion.created_by == "migration",
            )
        ).all()
        assert migration_snapshots

        apply_migrations(engine)
        session.refresh(template)
        assert template.version == version_after_first
