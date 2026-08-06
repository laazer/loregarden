import json

from loregarden.models.domain import StudioWorkflow, WorkflowTemplate, WorkflowTemplateVersion
from loregarden.services.studio_service import StudioService
from sqlmodel import select


def _workflow_stage(
    skill_name: str = "", *, route_skill: str = "", parallel_skill: str = ""
) -> dict:
    return {
        "key": "work",
        "name": "Work",
        "order": 1,
        "stage_type": "parallel" if parallel_skill else "classify",
        "agent_id": "backend_implementer",
        "skill_name": skill_name,
        "terminal": True,
        "classify_routes": [
            {
                "agent_id": "backend_implementer",
                "skill_name": route_skill,
                "default": True,
            }
        ],
        "parallel_agents": [{"agent_id": "backend_implementer", "skill_name": parallel_skill}]
        if parallel_skill
        else [],
    }


def _add_workflow(db_session, slug: str, stage: dict) -> StudioWorkflow:
    workflow = StudioWorkflow(
        slug=slug,
        name=slug,
        stages_json=json.dumps([stage]),
        transitions_json="[]",
    )
    db_session.add(workflow)
    db_session.commit()
    db_session.refresh(workflow)
    return workflow


def test_get_workflow_view_has_empty_stripped_skills(db_session):
    workflow = _add_workflow(db_session, "get-clean", _workflow_stage("missing-skill"))

    view = StudioService(db_session).get_workflow(workflow.slug)

    assert view.stripped_skills == []
    assert view.stages[0].skill_name == "missing-skill"


def test_publish_strips_unresolvable_skills_from_every_slot_and_reports_once(db_session):
    workflow = _add_workflow(
        db_session,
        "strip-on-publish",
        _workflow_stage(
            "missing-skill", route_skill="missing-skill", parallel_skill="missing-skill"
        ),
    )

    view = StudioService(db_session).publish_workflow(workflow.slug)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.id == workflow.published_template_id)
    ).one()
    stage = json.loads(template.stages_json)[0]

    assert view.stripped_skills == ["missing-skill"]
    assert stage["skill_name"] == ""
    assert stage["classify_routes"][0]["skill_name"] == ""
    assert stage["parallel_agents"][0]["skill_name"] == ""


def test_publish_preserves_resolvable_skills(db_session):
    workflow = _add_workflow(
        db_session,
        "publish-clean",
        _workflow_stage("plan", route_skill="refactor", parallel_skill="autopilot"),
    )

    view = StudioService(db_session).publish_workflow(workflow.slug)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.id == workflow.published_template_id)
    ).one()
    stage = json.loads(template.stages_json)[0]

    assert view.stripped_skills == []
    assert stage["skill_name"] == "plan"
    assert stage["classify_routes"][0]["skill_name"] == "refactor"
    assert stage["parallel_agents"][0]["skill_name"] == "autopilot"


def test_restore_strips_unresolvable_snapshot_skill_names(db_session):
    workflow = _add_workflow(db_session, "restore-strip", _workflow_stage("plan"))
    StudioService(db_session).publish_workflow(workflow.slug)
    db_session.refresh(workflow)
    template = db_session.get(WorkflowTemplate, workflow.published_template_id)
    version = WorkflowTemplateVersion(
        template_id=template.id,
        version=template.version + 1,
        snapshot_json=json.dumps(
            {
                "name": template.name,
                "description": template.description,
                "stages_json": json.dumps(
                    [
                        _workflow_stage(
                            "missing-skill",
                            route_skill="missing-route",
                            parallel_skill="missing-parallel",
                        )
                    ]
                ),
                "transitions_json": template.transitions_json,
            }
        ),
        created_by="test",
    )
    db_session.add(version)
    db_session.commit()

    view = StudioService(db_session).restore_workflow_version(workflow.slug, version.version)
    stage = view.stages[0]

    assert view.stripped_skills == ["missing-parallel", "missing-route", "missing-skill"]
    assert stage.skill_name == ""
    assert stage.classify_routes[0].skill_name == ""
    assert stage.parallel_agents[0].skill_name == ""
