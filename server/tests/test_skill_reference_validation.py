"""Skill references are validated where they are written, not where they render.

Before this, a dangling skill name saved cleanly from the Studio and raised
SkillNotFoundError at prompt-build time — a run that died several steps from the
edit that caused it. These pin the rejection at the write.
"""

import json
import logging

import pytest
from loregarden.models.domain import (
    ClassifyRoute,
    ParallelAgentSpec,
    StudioAgentCreate,
    StudioAgentUpdate,
    StudioWorkflow,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    StudioWorkflowUpdate,
)
from loregarden.services.studio_generation import parse_agent_generate_payload
from loregarden.services.studio_service import StudioService
from sqlmodel import select

MISSING = "this-skill-does-not-exist"


def _terminal_stage(order: int = 9) -> StudioWorkflowStage:
    return StudioWorkflowStage(key="done", name="Done", terminal=True, order=order)


def _agent_body(**overrides) -> StudioAgentCreate:
    body = {
        "slug": "skill-ref-agent",
        "name": "Skill Ref Agent",
        "description": "d",
        "role_body": "b",
        "adapter": "claude",
    }
    body.update(overrides)
    return StudioAgentCreate(**body)


def test_create_agent_rejects_unknown_default_skill(db_session):
    with pytest.raises(ValueError, match=MISSING):
        StudioService(db_session).create_agent(_agent_body(default_skill=MISSING))


def test_create_agent_accepts_a_real_skill(db_session):
    view = StudioService(db_session).create_agent(_agent_body(default_skill="plan"))
    assert view.default_skill == "plan"


def test_create_agent_accepts_no_skill(db_session):
    view = StudioService(db_session).create_agent(_agent_body(slug="no-skill-agent"))
    assert view.default_skill == ""


def test_update_agent_rejects_unknown_default_skill(db_session):
    service = StudioService(db_session)
    service.create_agent(_agent_body(default_skill="plan"))
    with pytest.raises(ValueError, match=MISSING):
        service.update_agent("skill-ref-agent", StudioAgentUpdate(default_skill=MISSING))


@pytest.mark.parametrize(
    "stage",
    [
        pytest.param(
            StudioWorkflowStage(
                key="s1", name="S1", agent_id="planner", skill_name=MISSING, order=1
            ),
            id="stage-skill",
        ),
        pytest.param(
            StudioWorkflowStage(
                key="s1",
                name="S1",
                stage_type="classify",
                agent_id="planner",
                order=1,
                classify_routes=[
                    ClassifyRoute(agent_id="planner", skill_name=MISSING, default=True)
                ],
            ),
            id="classify-route-skill",
        ),
        pytest.param(
            StudioWorkflowStage(
                key="s1",
                name="S1",
                stage_type="parallel",
                agent_id="planner",
                order=1,
                parallel_agents=[ParallelAgentSpec(agent_id="planner", skill_name=MISSING)],
            ),
            id="parallel-member-skill",
        ),
    ],
)
def test_create_workflow_rejects_unknown_skill_anywhere_in_a_stage(db_session, stage):
    body = StudioWorkflowCreate(
        slug="bad-wf", name="Bad WF", description="d", stages=[stage, _terminal_stage()]
    )
    with pytest.raises(ValueError, match=MISSING):
        StudioService(db_session).create_workflow(body)


def test_create_workflow_accepts_real_skills(db_session):
    body = StudioWorkflowCreate(
        slug="good-wf",
        name="Good WF",
        description="d",
        stages=[
            StudioWorkflowStage(
                key="s1", name="S1", agent_id="planner", skill_name="plan", order=1
            ),
            _terminal_stage(),
        ],
    )
    view = StudioService(db_session).create_workflow(body)
    assert view.stages[0].skill_name == "plan"


def test_update_workflow_rejects_unknown_skill(db_session):
    service = StudioService(db_session)
    service.create_workflow(
        StudioWorkflowCreate(
            slug="edit-wf", name="Edit WF", description="d", stages=[_terminal_stage()]
        )
    )
    body = StudioWorkflowUpdate(
        stages=[
            StudioWorkflowStage(
                key="s1", name="S1", agent_id="planner", skill_name=MISSING, order=1
            ),
            _terminal_stage(),
        ]
    )
    with pytest.raises(ValueError, match=MISSING):
        service.update_workflow("edit-wf", body)


def test_publish_workflow_rejects_a_dangling_skill_saved_before_validation(db_session):
    """A workflow row written before this check existed must not publish. Publish
    is the last gate before a template becomes what runs, so it re-validates
    rather than trusting what is already stored."""
    service = StudioService(db_session)
    service.create_workflow(
        StudioWorkflowCreate(
            slug="legacy-wf", name="Legacy WF", description="d", stages=[_terminal_stage()]
        )
    )
    workflow = db_session.exec(
        select(StudioWorkflow).where(StudioWorkflow.slug == "legacy-wf")
    ).one()
    workflow.stages_json = json.dumps(
        [
            StudioWorkflowStage(
                key="s1", name="S1", agent_id="planner", skill_name=MISSING, order=1
            ).model_dump(),
            _terminal_stage().model_dump(),
        ]
    )
    db_session.add(workflow)
    db_session.commit()

    with pytest.raises(ValueError, match=MISSING):
        service.publish_workflow("legacy-wf")


def test_generated_agent_drops_unknown_default_skill_and_says_so(caplog):
    payload = json.dumps(
        {
            "name": "Bogus",
            "slug": "bogus",
            "description": "d",
            "role_body": "b",
            "adapter": "claude",
            "default_skill": MISSING,
            "mcp_tools": [],
        }
    )
    with caplog.at_level(logging.WARNING):
        generated = parse_agent_generate_payload(f"```json\n{payload}\n```", skills=["plan"])
    assert generated is not None
    assert generated.default_skill == ""
    assert MISSING in caplog.text


def test_generated_agent_keeps_a_real_default_skill(caplog):
    payload = json.dumps(
        {
            "name": "Fine",
            "slug": "fine",
            "description": "d",
            "role_body": "b",
            "adapter": "claude",
            "default_skill": "plan",
            "mcp_tools": [],
        }
    )
    with caplog.at_level(logging.WARNING):
        generated = parse_agent_generate_payload(f"```json\n{payload}\n```", skills=["plan"])
    assert generated is not None
    assert generated.default_skill == "plan"
    assert caplog.text == ""
