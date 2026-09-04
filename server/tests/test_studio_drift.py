"""A Studio draft must not silently roll back the workflow it publishes to.

`studio_workflows` holds the editable draft, `workflow_templates` holds what the
orchestrator runs, and `publish_workflow` overwrites the second from the first.
The `loregarden-tdd-v3` draft was once 9 stages against a live 12-stage template:
pressing publish would have dropped `plan-synthesis`, `verify` and the terminal
stage. It was repaired by hand and nothing prevented a recurrence.
"""

import json

import pytest
from loregarden.models.domain import (
    DoctorStatus,
    StudioWorkflow,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.doctor import check_studio_draft_drift
from loregarden.services.studio_drift import (
    StageRemovalNeedsConfirmation,
    detect_all_drift,
    detect_drift,
)
from loregarden.services.studio_service import StudioService
from sqlmodel import Session, select

SLUG = "drift-demo"


def _stages() -> list[StudioWorkflowStage]:
    return [
        StudioWorkflowStage(key="plan", name="Plan", order=1, agent_id="planner"),
        StudioWorkflowStage(key="verify", name="Verify", order=2, agent_id="verifier"),
        StudioWorkflowStage(key="done", name="Done", order=3, terminal=True),
    ]


def _published(db_session: Session, slug: str = SLUG) -> StudioWorkflow:
    svc = StudioService(db_session)
    svc.create_workflow(StudioWorkflowCreate(slug=slug, name=slug, stages=_stages()))
    svc.publish_workflow(slug)
    return db_session.exec(select(StudioWorkflow).where(StudioWorkflow.slug == slug)).one()


def _rewrite_draft(db_session: Session, workflow: StudioWorkflow, stages: list[dict]) -> None:
    """Edit the draft behind the service, which is what a rollback looks like."""
    workflow.stages_json = json.dumps(stages)
    db_session.add(workflow)
    db_session.commit()
    db_session.refresh(workflow)


def _draft_dicts(workflow: StudioWorkflow) -> list[dict]:
    return json.loads(workflow.stages_json)


# --- AC1: the detector ------------------------------------------------------


def test_a_freshly_published_draft_is_not_drifted(db_session: Session):
    """The control for every test below. Without it, a detector that reported
    drift unconditionally would pass all of them."""
    drift = detect_drift(db_session, _published(db_session))
    assert drift.published is True
    assert drift.drifted is False
    assert drift.stages_removed == []


def test_an_unpublished_draft_is_not_drift(db_session: Session):
    """Never published is not drifted — there is nothing to have drifted from."""
    StudioService(db_session).create_workflow(
        StudioWorkflowCreate(slug="never", name="never", stages=_stages())
    )
    workflow = db_session.exec(select(StudioWorkflow).where(StudioWorkflow.slug == "never")).one()
    drift = detect_drift(db_session, workflow)
    assert drift.published is False
    assert drift.drifted is False


def test_a_draft_rolled_back_by_one_stage_is_reported(db_session: Session):
    """AC5. The 9-vs-12 case that motivated the ticket, in miniature."""
    workflow = _published(db_session)
    _rewrite_draft(
        db_session, workflow, [s for s in _draft_dicts(workflow) if s["key"] != "verify"]
    )

    drift = detect_drift(db_session, workflow)
    assert drift.drifted is True
    assert drift.stages_removed == ["verify"]
    assert drift.stages_added == []


def test_a_field_changed_inside_a_stage_is_reported(db_session: Session):
    """Equal counts, equal keys, equal transitions — and still drift.

    This is the shape migration 0108 produced: it grouped the template's two
    implementation stages, and had it not also written the draft, nothing that
    compares stage-key sets or transition counts would have seen a difference.
    """
    workflow = _published(db_session)
    stages = _draft_dicts(workflow)
    next(s for s in stages if s["key"] == "verify")["optional"] = True
    _rewrite_draft(db_session, workflow, stages)

    drift = detect_drift(db_session, workflow)
    assert drift.drifted is True
    assert drift.stages_added == [] and drift.stages_removed == []
    assert [d.field for d in drift.stages_changed["verify"]] == ["optional"]


def test_a_stage_field_added_to_the_model_later_is_not_reported_as_drift(db_session: Session):
    """A stored row written before a field existed must compare equal to one
    written after it. Otherwise the day a field is added, every draft reports
    drifted — a false positive that teaches people to ignore the check."""
    workflow = _published(db_session)
    stages = _draft_dicts(workflow)
    for stage in stages:
        stage.pop("stage_brief", None)
        stage.pop("alternative_group", None)
    _rewrite_draft(db_session, workflow, stages)

    assert detect_drift(db_session, workflow).drifted is False


def test_transition_counts_are_compared(db_session: Session):
    workflow = _published(db_session)
    workflow.transitions_json = json.dumps([{"from": "plan", "to": "verify"}])
    db_session.add(workflow)
    db_session.commit()

    drift = detect_drift(db_session, workflow)
    assert drift.drifted is True
    assert drift.draft_transition_count != drift.template_transition_count


def test_detect_all_drift_covers_every_draft(db_session: Session):
    _published(db_session, "one")
    _published(db_session, "two")
    assert [d.slug for d in detect_all_drift(db_session)] == ["one", "two"]


# --- AC3: publishing over live tickets --------------------------------------


def _ticket_sitting_on(db_session: Session, workflow: StudioWorkflow, stage_key: str) -> Ticket:
    """A ticket whose instance reads the LIVE template (template_version NULL).

    That is the case that matters: `get_template_stages_at_version` falls back to
    the live template whenever the pin is NULL, and 152 of 790 live instances
    were NULL when this was written.
    """
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    template = db_session.get(WorkflowTemplate, workflow.published_template_id)
    assert template is not None
    ticket = Ticket(
        external_id=f"drift-{stage_key}",
        workspace_id=ws.id,
        title="Mid-workflow",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=stage_key,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            template_version=None,
            current_stage_key=stage_key,
            stages_json="{}",
        )
    )
    db_session.commit()
    return ticket


def test_publishing_over_a_stage_a_live_ticket_is_on_needs_confirmation(db_session: Session):
    """AC3 and AC5."""
    workflow = _published(db_session)
    _ticket_sitting_on(db_session, workflow, "verify")
    _rewrite_draft(
        db_session, workflow, [s for s in _draft_dicts(workflow) if s["key"] != "verify"]
    )

    with pytest.raises(StageRemovalNeedsConfirmation) as excinfo:
        StudioService(db_session).publish_workflow(SLUG)
    assert "verify" in str(excinfo.value)
    assert "drift-verify" in str(excinfo.value), "the refusal must name the tickets at risk"


def test_confirming_lets_the_publish_through(db_session: Session):
    workflow = _published(db_session)
    _ticket_sitting_on(db_session, workflow, "verify")
    _rewrite_draft(
        db_session, workflow, [s for s in _draft_dicts(workflow) if s["key"] != "verify"]
    )

    StudioService(db_session).publish_workflow(SLUG, confirm_stage_removal=True)
    template = db_session.get(WorkflowTemplate, workflow.published_template_id)
    assert "verify" not in {s["key"] for s in json.loads(template.stages_json)}


def test_removing_a_stage_nobody_is_on_publishes_without_confirmation(db_session: Session):
    """The control for the guard: a routine edit must not raise a prompt.

    A confirmation that fires on every stage removal becomes one people click
    through, which is worse than not having it.
    """
    workflow = _published(db_session)
    _ticket_sitting_on(db_session, workflow, "plan")
    _rewrite_draft(
        db_session, workflow, [s for s in _draft_dicts(workflow) if s["key"] != "verify"]
    )

    StudioService(db_session).publish_workflow(SLUG)


def test_a_finished_ticket_does_not_block_a_publish(db_session: Session):
    workflow = _published(db_session)
    ticket = _ticket_sitting_on(db_session, workflow, "verify")
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()
    _rewrite_draft(
        db_session, workflow, [s for s in _draft_dicts(workflow) if s["key"] != "verify"]
    )

    StudioService(db_session).publish_workflow(SLUG)


def test_a_version_pinned_ticket_does_not_block_a_publish(db_session: Session):
    """A pinned instance resolves through its snapshot, so a template edit cannot
    strand it. Only the unpinned ones are exposed."""
    workflow = _published(db_session)
    ticket = _ticket_sitting_on(db_session, workflow, "verify")
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    instance.template_version = 1
    db_session.add(instance)
    db_session.commit()
    _rewrite_draft(
        db_session, workflow, [s for s in _draft_dicts(workflow) if s["key"] != "verify"]
    )

    StudioService(db_session).publish_workflow(SLUG)


# --- AC4: the doctor check --------------------------------------------------


def test_the_doctor_check_reports_a_drifted_draft(db_session: Session, tmp_path):
    workflow = _published(db_session)
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    assert check_studio_draft_drift(db_session, ws, tmp_path).status is DoctorStatus.PASS

    _rewrite_draft(
        db_session, workflow, [s for s in _draft_dicts(workflow) if s["key"] != "verify"]
    )
    finding = check_studio_draft_drift(db_session, ws, tmp_path)
    assert finding.status is DoctorStatus.FAIL
    assert SLUG in finding.finding
