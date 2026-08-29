"""Publishing a Studio workflow must round-trip every stage field.

`publish_workflow` builds the template's stage dict field by field, so a field
`WorkflowStageDef` carries and the publish payload omits is dropped the first
time anyone publishes — silently, and only visible later as a gate that stopped
firing. `terminal` and `skip_when` were lost that way once; `required_evidence`
was next in line, and it is what makes the implement and verify stages of the
live loregarden template prove their work.
"""

import json

from loregarden.core.workflow_loader import get_template_stages
from loregarden.models.domain import (
    StudioWorkflowCreate,
    StudioWorkflowStage,
    WorkflowTemplate,
)
from loregarden.services.studio_service import StudioService
from sqlmodel import Session, select

# Every field StudioWorkflowStage carries beyond the identity ones, set to a
# value distinguishable from its default.
RICH_STAGE = {
    "stage_type": "agent",
    "agent_id": "planner",
    "optional": True,
    "gate_required": True,
    "skip_when": "has_description",
    "model": "claude-opus-5",
    "required_evidence": ["real_surface"],
    "checklist": ["Run the thing"],
    "stage_brief": "Do the part the reviewer cannot do for you.",
}


def _publish(session: Session, slug: str) -> list:
    svc = StudioService(session)
    svc.create_workflow(
        StudioWorkflowCreate(
            slug=slug,
            name="Round Trip",
            stages=[
                StudioWorkflowStage(key="work", name="Work", order=1, **RICH_STAGE),
                StudioWorkflowStage(key="done", name="Done", order=2, terminal=True),
            ],
        )
    )
    svc.publish_workflow(slug)
    template = session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == f"studio-{slug}")
    ).one()
    return get_template_stages(template)


def test_publish_round_trips_every_stage_field(db_session: Session):
    stages = {stage.key: stage for stage in _publish(db_session, "round-trip")}
    work = stages["work"]

    for field, expected in RICH_STAGE.items():
        assert getattr(work, field) == expected, f"publish dropped {field}"
    assert stages["done"].terminal is True


def test_published_json_carries_the_evidence_requirement(db_session: Session):
    """The field that matters most in practice, asserted on the stored JSON.

    `get_template_stages` fills defaults, so a stage that lost its evidence
    requirement still parses — it just parses as an empty list. Read the row.
    """
    _publish(db_session, "round-trip-json")
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-round-trip-json")
    ).one()
    raw = {item["key"]: item for item in json.loads(template.stages_json)}

    assert raw["work"]["required_evidence"] == ["real_surface"]
    assert raw["work"]["stage_brief"] == RICH_STAGE["stage_brief"]
    assert raw["work"]["checklist"] == ["Run the thing"]
