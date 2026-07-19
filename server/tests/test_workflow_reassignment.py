"""Reassigning a workflow says what it will destroy first (U4e)."""

from loregarden.core.workflow_loader import sync_workflow_templates
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.workflow_reassignment import describe_reassignment
from loregarden.services.workflow_service import WorkflowService
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select


def _ticket(db_session: Session) -> Ticket:
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    ticket = Ticket(
        external_id="reassign-me",
        workspace_id=ws.id,
        title="Reassign me",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _put_on(db_session: Session, ticket: Ticket, slug: str) -> WorkflowInstance:
    WorkflowService(db_session).set_ticket_workflow_template(ticket, slug)
    db_session.commit()
    return db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()


def test_a_ticket_that_has_not_started_is_not_destructive(client, db_session: Session):
    """Commit-time assignment must not prompt: there is nothing to lose."""
    sync_workflow_templates(db_session)
    ticket = _ticket(db_session)
    _put_on(db_session, ticket, "loregarden-tdd")

    summary = describe_reassignment(db_session, ticket, "extended-tdd")
    assert summary["destructive"] is False
    assert summary["completed_stages"] == []


def test_completed_work_is_named_not_just_counted(client, db_session: Session):
    """An operator needs to see what is lost, not that something is."""
    sync_workflow_templates(db_session)
    ticket = _ticket(db_session)
    instance = _put_on(db_session, ticket, "loregarden-tdd")

    template = db_session.get(WorkflowTemplate, instance.template_id)
    from loregarden.core.workflow_loader import get_template_stages

    stages = get_template_stages(template)
    done, running = stages[0].key, stages[1].key
    instance.stages_json = initial_stages_json(stages)
    import json

    entries = json.loads(instance.stages_json)
    for entry in entries:
        if entry["key"] == done:
            entry["status"] = StageStatus.DONE.value
        if entry["key"] == running:
            entry["status"] = StageStatus.RUNNING.value
    instance.stages_json = json.dumps(entries)
    db_session.add(instance)
    db_session.commit()

    summary = describe_reassignment(db_session, ticket, "extended-tdd")
    assert summary["destructive"] is True
    assert done in summary["completed_stages"]
    # Work in flight counts as loss too, not just finished work.
    assert running in summary["completed_stages"]
    assert summary["current_template_slug"] == "loregarden-tdd"
    assert summary["target_template_slug"] == "extended-tdd"
    assert summary["resets_to_stage_key"]


def test_reassigning_to_the_same_template_still_warns(client, db_session: Session):
    """It rewrites the stage map either way, so it is destructive on the same terms."""
    sync_workflow_templates(db_session)
    ticket = _ticket(db_session)
    instance = _put_on(db_session, ticket, "loregarden-tdd")

    import json

    entries = json.loads(instance.stages_json)
    entries[0]["status"] = StageStatus.DONE.value
    instance.stages_json = json.dumps(entries)
    db_session.add(instance)
    db_session.commit()

    assert describe_reassignment(db_session, ticket, "loregarden-tdd")["destructive"] is True


def test_skipped_stages_are_not_counted_as_loss(client, db_session: Session):
    """A stage the workflow chose to skip loses nothing by being reset."""
    sync_workflow_templates(db_session)
    ticket = _ticket(db_session)
    instance = _put_on(db_session, ticket, "loregarden-tdd")

    import json

    entries = json.loads(instance.stages_json)
    for entry in entries:
        entry["status"] = StageStatus.WONT_DO.value
    instance.stages_json = json.dumps(entries)
    db_session.add(instance)
    db_session.commit()

    assert describe_reassignment(db_session, ticket, "extended-tdd")["destructive"] is False


def test_endpoint_reports_the_cost_without_applying_it(client, db_session: Session):
    sync_workflow_templates(db_session)
    ticket = _ticket(db_session)
    instance = _put_on(db_session, ticket, "loregarden-tdd")
    before = instance.template_id

    res = client.get(
        f"/api/tickets/{ticket.id}/workflow-reassignment",
        params={"template_slug": "extended-tdd"},
    )
    assert res.status_code == 200
    assert res.json()["target_template_slug"] == "extended-tdd"

    db_session.refresh(instance)
    # Read-only: the preview must not move the ticket.
    assert instance.template_id == before
