"""Runtime stage pruning: a run may declare an optional stage won't-do.

The template stays static; what a run decides is whether a stage its author
already marked optional applies to *this* ticket. Three things had to hold
before that was safe to offer an agent: a required stage cannot be pruned (a
WONT_DO stage counts as resolved, so pruning the rest of a workflow derives a
DONE ticket), the reason survives on the stage instead of on
`ticket.blocking_issues` (which `_derive_ticket_state` reads as BLOCKED), and
the decision leaves an event behind.
"""

import pytest
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    DomainEvent,
    EventType,
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.studio_routing import is_prunable_stage, prunable_stage_keys
from loregarden.services.workflow_state import (
    build_stage_views,
    initial_stages_json,
    parse_stage_map,
    parse_stage_notes,
    serialize_stage_map,
    set_stage_status,
)
from sqlmodel import Session, select

REQUIRED_STAGE = "specification"
OPTIONAL_STAGE = "playtest"
CURSOR_STAGE = "implementation"


def _setup(db_session: Session, *, external_id: str):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    stages = get_template_stages(template)

    ticket = Ticket(
        external_id=external_id,
        workspace_id=ws.id,
        title="Stage pruning test",
        description="Exercise runtime stage pruning",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=CURSOR_STAGE,
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key=CURSOR_STAGE,
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    orch_run = OrchestrationRun(
        run_code=f"orch_{external_id}",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key=CURSOR_STAGE,
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add(orch_run)
    db_session.commit()

    # The caller's own stage is mid-run, which is when an agent would call
    # skip_stage — and the state in which a reason on blocking_issues blocked
    # the ticket.
    set_stage_status(ticket, instance, stages, CURSOR_STAGE, StageStatus.RUNNING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    return ticket, instance, stages, orch_run


def _stage_map(db_session: Session, ticket: Ticket, stages):
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    return parse_stage_map(instance, stages)


def test_optional_stage_is_pruned_with_its_reason_and_no_block(db_session: Session):
    ticket, instance, stages, orch_run = _setup(db_session, external_id="prune-optional")
    svc = OrchestrationCallbackService(db_session)

    svc.skip_stage(
        orch_run, ticket, stage_key=OPTIONAL_STAGE, reason="No player-facing change to play test"
    )

    db_session.refresh(ticket)
    assert _stage_map(db_session, ticket, stages)[OPTIONAL_STAGE] == StageStatus.WONT_DO
    # The reason lands on the stage, not on the ticket — blocking_issues is read
    # by _derive_ticket_state and would have flipped a mid-run ticket to BLOCKED.
    assert ticket.blocking_issues == ""
    assert ticket.state != TicketState.BLOCKED

    db_session.refresh(instance)
    views = {v.key: v for v in build_stage_views(ticket, instance, stages)}
    assert views[OPTIONAL_STAGE].note == "No player-facing change to play test"

    events = db_session.exec(
        select(DomainEvent).where(
            DomainEvent.ticket_id == ticket.id,
            DomainEvent.type == EventType.STAGE_SKIPPED,
        )
    ).all()
    assert len(events) == 1


def test_required_stage_cannot_be_pruned(db_session: Session):
    ticket, _instance, stages, orch_run = _setup(db_session, external_id="prune-required")
    svc = OrchestrationCallbackService(db_session)

    with pytest.raises(ValueError) as exc:
        svc.skip_stage(orch_run, ticket, stage_key=REQUIRED_STAGE, reason="feels unnecessary")

    message = str(exc.value)
    assert REQUIRED_STAGE in message
    # The refusal names what the workflow does offer, so the next call is not a guess.
    assert OPTIONAL_STAGE in message
    assert _stage_map(db_session, ticket, stages)[REQUIRED_STAGE] == StageStatus.PENDING


def test_running_and_done_stages_cannot_be_pruned(db_session: Session):
    ticket, instance, stages, orch_run = _setup(db_session, external_id="prune-in-flight")
    svc = OrchestrationCallbackService(db_session)

    set_stage_status(ticket, instance, stages, OPTIONAL_STAGE, StageStatus.RUNNING)
    db_session.commit()
    with pytest.raises(ValueError, match="complete_stage"):
        svc.skip_stage(orch_run, ticket, stage_key=OPTIONAL_STAGE)

    set_stage_status(ticket, instance, stages, OPTIONAL_STAGE, StageStatus.DONE)
    db_session.commit()
    with pytest.raises(ValueError, match="already ran"):
        svc.skip_stage(orch_run, ticket, stage_key=OPTIONAL_STAGE)


def test_unknown_stage_key_is_refused(db_session: Session):
    ticket, _instance, _stages, orch_run = _setup(db_session, external_id="prune-unknown")
    svc = OrchestrationCallbackService(db_session)

    with pytest.raises(ValueError, match="Unknown stage key"):
        svc.skip_stage(orch_run, ticket, stage_key="playtesting")


def test_cursor_moves_off_a_stage_it_was_parked_on(db_session: Session):
    ticket, instance, stages, orch_run = _setup(db_session, external_id="prune-cursor")
    svc = OrchestrationCallbackService(db_session)

    set_stage_status(ticket, instance, stages, CURSOR_STAGE, StageStatus.DONE)
    ticket.workflow_stage_key = OPTIONAL_STAGE
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()

    svc.skip_stage(orch_run, ticket, stage_key=OPTIONAL_STAGE, reason="not applicable")

    db_session.refresh(ticket)
    assert ticket.workflow_stage_key != OPTIONAL_STAGE


def test_a_later_status_write_does_not_erase_the_reason(db_session: Session):
    """Regression: `serialize_stage_map` dropped notes, so the next status write
    erased every reason `build_stage_views` was there to render."""
    ticket, instance, stages, orch_run = _setup(db_session, external_id="prune-note-survives")
    svc = OrchestrationCallbackService(db_session)
    svc.skip_stage(orch_run, ticket, stage_key=OPTIONAL_STAGE, reason="nothing to play test")

    db_session.refresh(instance)
    set_stage_status(ticket, instance, stages, REQUIRED_STAGE, StageStatus.DONE)
    db_session.commit()

    db_session.refresh(instance)
    assert parse_stage_notes(instance)[OPTIONAL_STAGE] == "nothing to play test"


def test_terminal_stage_is_never_prunable():
    """Pruning the terminal stage removes the only stage that ends the workflow."""
    terminal = WorkflowStageDef(key="done", name="Done", order=9, optional=True, terminal=True)
    optional = WorkflowStageDef(key="playtest", name="Playtest", order=8, optional=True)
    required = WorkflowStageDef(key="implement", name="Implement", order=7)

    assert not is_prunable_stage(terminal)
    assert is_prunable_stage(optional)
    assert not is_prunable_stage(required)
    assert prunable_stage_keys([terminal, optional, required]) == ["playtest"]


def test_serialize_omits_notes_for_stages_that_have_none():
    stages = [
        WorkflowStageDef(key="a", name="A", order=1),
        WorkflowStageDef(key="b", name="B", order=2),
    ]
    stage_map = {"a": StageStatus.DONE, "b": StageStatus.WONT_DO}

    payload = serialize_stage_map(stage_map, stages, notes={"b": "why"})

    assert '"note"' in payload
    assert payload.count('"note"') == 1
