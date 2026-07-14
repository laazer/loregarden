"""Rejecting a human workflow-gate approval (e.g. Playtest sign-off) must reroute
the ticket per the template's `reject` transition, not just hard-block the same
stage in place — previously there was no way to mark a stage like `playtest` as
failed and have it route back to `implementation` for another pass.
"""

from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration import ApprovalService
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select


def _setup_playtest_ticket(db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="playtest-reject-test",
        workspace_id=ws.id,
        title="Playtest reject test",
        description="Verify rejecting the playtest gate reroutes to implementation",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="playtest",
        workflow_stage_status=StageStatus.AWAITING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    stage_map = {s.key: StageStatus.DONE for s in stages if s.key != "playtest"}
    stage_map["playtest"] = StageStatus.AWAITING
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="playtest",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ws.id,
        kind=ApprovalKind.WORKFLOW_GATE,
        stage_key="playtest",
        title="Playtest sign-off",
        impact="Human review required before the workflow advances.",
        status=ApprovalStatus.PENDING,
    )
    db_session.add(approval)
    db_session.commit()
    db_session.refresh(approval)

    return ticket, instance, stages, approval


def test_rejecting_playtest_gate_reroutes_to_implementation(db_session: Session):
    ticket, instance, stages, approval = _setup_playtest_ticket(db_session)

    svc = ApprovalService(db_session)
    svc.resolve(approval.id, approved=False, response_text="Movement felt broken in the final area")

    db_session.refresh(ticket)
    db_session.refresh(instance)
    stage_map = parse_stage_map(instance, stages)

    assert ticket.workflow_stage_key == "implementation"
    assert stage_map["implementation"] == StageStatus.PENDING
    assert "movement felt broken" in ticket.blocking_issues.lower()


def test_approving_playtest_gate_still_marks_stage_done(db_session: Session):
    ticket, instance, stages, approval = _setup_playtest_ticket(db_session)

    svc = ApprovalService(db_session)
    svc.resolve(approval.id, approved=True)

    db_session.refresh(ticket)
    db_session.refresh(instance)
    stage_map = parse_stage_map(instance, stages)

    assert stage_map["playtest"] == StageStatus.DONE


def test_approving_playtest_gate_with_rework_route_reroutes_upstream(db_session: Session):
    """Approve the playtest but send the ticket back so prototype fixes get
    formalized with production code and tests."""
    ticket, instance, stages, approval = _setup_playtest_ticket(db_session)

    svc = ApprovalService(db_session)
    svc.resolve(
        approval.id,
        approved=True,
        response_text="Playtest tuning changes need real implementation and tests",
        route_to_stage_key="test_design",
    )

    db_session.refresh(ticket)
    db_session.refresh(instance)
    db_session.refresh(approval)
    stage_map = parse_stage_map(instance, stages)

    assert approval.status == ApprovalStatus.APPROVED
    assert ticket.workflow_stage_key == "test_design"
    assert stage_map["test_design"] == StageStatus.PENDING
    assert stage_map["implementation"] == StageStatus.PENDING
    assert stage_map["playtest"] == StageStatus.PENDING
    assert "gate approved with rework" in ticket.blocking_issues
    assert "real implementation and tests" in ticket.blocking_issues


def test_rework_route_without_note_uses_default_context(db_session: Session):
    ticket, instance, stages, approval = _setup_playtest_ticket(db_session)

    svc = ApprovalService(db_session)
    svc.resolve(approval.id, approved=True, route_to_stage_key="implementation")

    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == "implementation"
    assert "formalize the prototype changes" in ticket.blocking_issues.lower()


def test_rework_route_rejects_downstream_stage(db_session: Session):
    ticket, instance, stages, approval = _setup_playtest_ticket(db_session)

    svc = ApprovalService(db_session)
    try:
        svc.resolve(approval.id, approved=True, route_to_stage_key="learning")
        raise AssertionError("Expected ValueError for downstream rework target")
    except ValueError as exc:
        assert "must come before" in str(exc)

    db_session.refresh(approval)
    db_session.refresh(ticket)
    assert approval.status == ApprovalStatus.PENDING
    assert ticket.workflow_stage_key == "playtest"


def test_rework_route_rejects_unknown_stage(db_session: Session):
    ticket, instance, stages, approval = _setup_playtest_ticket(db_session)

    svc = ApprovalService(db_session)
    try:
        svc.resolve(approval.id, approved=True, route_to_stage_key="not-a-stage")
        raise AssertionError("Expected ValueError for unknown rework target")
    except ValueError as exc:
        assert "Unknown rework stage key" in str(exc)

    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.PENDING


def test_rejecting_gate_with_explicit_route_overrides_default(db_session: Session):
    """Rejecting normally falls back to the template's reject transition
    (implementation, per _setup_playtest_ticket's template) — an explicit
    route_to_stage_key should override that default."""
    ticket, instance, stages, approval = _setup_playtest_ticket(db_session)

    svc = ApprovalService(db_session)
    svc.resolve(
        approval.id,
        approved=False,
        response_text="Tuning is fine but the test plan missed the fusion combo",
        route_to_stage_key="test_design",
    )

    db_session.refresh(ticket)
    db_session.refresh(instance)
    db_session.refresh(approval)
    stage_map = parse_stage_map(instance, stages)

    assert approval.status == ApprovalStatus.REJECTED
    assert ticket.workflow_stage_key == "test_design"
    assert stage_map["test_design"] == StageStatus.PENDING
    assert stage_map["implementation"] == StageStatus.PENDING
    assert "fusion combo" in ticket.blocking_issues.lower()


def test_reject_route_rejects_downstream_stage(db_session: Session):
    ticket, instance, stages, approval = _setup_playtest_ticket(db_session)

    svc = ApprovalService(db_session)
    try:
        svc.resolve(approval.id, approved=False, route_to_stage_key="learning")
        raise AssertionError("Expected ValueError for downstream reject target")
    except ValueError as exc:
        assert "must come before" in str(exc)

    db_session.refresh(approval)
    db_session.refresh(ticket)
    assert approval.status == ApprovalStatus.PENDING
    assert ticket.workflow_stage_key == "playtest"


def test_pending_gate_approval_view_offers_upstream_route_options(db_session: Session):
    from loregarden.services.approval_views import approval_to_view

    ticket, instance, stages, approval = _setup_playtest_ticket(db_session)

    view = approval_to_view(db_session, approval)
    option_keys = [option["key"] for option in view["route_options"]]

    assert "implementation" in option_keys
    assert "test_design" in option_keys
    assert "playtest" not in option_keys
    assert "learning" not in option_keys
    assert "done" not in option_keys
