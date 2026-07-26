"""Ticket Studio can put a committed ticket on a chosen workflow (U4c)."""

import json

import pytest
from loregarden.core.workflow_loader import sync_workflow_templates
from loregarden.models.domain import (
    Ticket,
    TicketStudioSession,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.ticket_studio_service import TicketStudioService
from sqlmodel import Session, select


def _draft(**overrides):
    """feature -> capability -> task: the hierarchy the validator requires."""
    task = {
        "ref": "t1",
        "work_item_type": "task",
        "parent_ref": "c1",
        "title": "Wire the import parser",
        "description": "Already scoped here.",
        "acceptance_criteria": ["AC-1 parses markdown"],
        "priority": 3,
        "selected": True,
    }
    task.update(overrides)
    feature = {
        "ref": "f1",
        "work_item_type": "feature",
        "parent_ref": None,
        "title": "Markdown import",
        "description": "",
        "acceptance_criteria": [],
        "priority": 3,
        "selected": True,
    }
    capability = {
        "ref": "c1",
        "work_item_type": "capability",
        "parent_ref": "f1",
        "title": "Parser",
        "description": "",
        "acceptance_criteria": [],
        "priority": 3,
        "selected": True,
    }
    return [feature, capability, task]


def _ticket_by_title(db_session, ticket_ids, title):
    from loregarden.models.domain import Ticket

    for tid in ticket_ids:
        ticket = db_session.get(Ticket, tid)
        if ticket and ticket.title == title:
            return ticket
    raise AssertionError(f"no committed ticket titled {title!r}")


def _session_row(db_session: Session, workspace: Workspace, draft) -> TicketStudioSession:
    row = TicketStudioSession(
        workspace_id=workspace.id,
        title="Import work",
        brief="",
        draft_json=json.dumps(draft),
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _committed_template(db_session: Session, ticket_id: str) -> str:
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket_id)
    ).first()
    assert instance is not None
    template = db_session.get(WorkflowTemplate, instance.template_id)
    return template.slug


def test_draft_workflow_choice_is_applied_on_commit(client, db_session: Session):
    sync_workflow_templates(db_session)
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    row = _session_row(db_session, workspace, _draft(workflow_template_slug="extended-tdd"))

    result = TicketStudioService(db_session).commit_session(row.id)

    assert result.created_ticket_ids
    task = _ticket_by_title(db_session, result.created_ticket_ids, "Wire the import parser")
    feature = _ticket_by_title(db_session, result.created_ticket_ids, "Markdown import")
    # Only the item that asked for it moves; its parent keeps the default.
    assert _committed_template(db_session, task.id) == "extended-tdd"
    assert _committed_template(db_session, feature.id) != "extended-tdd"


def test_without_a_choice_the_workspace_default_still_applies(client, db_session: Session):
    """Existing drafts carry no slug, so commit must keep working unchanged."""
    sync_workflow_templates(db_session)
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    row = _session_row(db_session, workspace, _draft())

    result = TicketStudioService(db_session).commit_session(row.id)

    task = _ticket_by_title(db_session, result.created_ticket_ids, "Wire the import parser")
    assigned = _committed_template(db_session, task.id)
    workspace_default = db_session.get(WorkflowTemplate, workspace.workflow_template_id)
    assert assigned == workspace_default.slug


def test_an_unknown_workflow_is_rejected_rather_than_silently_ignored(client, db_session: Session):
    """A typo'd slug must not commit a ticket onto the wrong pipeline quietly."""
    sync_workflow_templates(db_session)
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    row = _session_row(db_session, workspace, _draft(workflow_template_slug="no-such-template"))

    with pytest.raises(ValueError):
        TicketStudioService(db_session).commit_session(row.id)


def test_commit_adds_integration_review_child_with_sibling_dependency(client, db_session: Session):
    """Committing a feature/capability/task draft gives the feature an
    integration-review capability, wired to depend on its capability sibling."""
    from loregarden.services.ticket_service import TicketService

    sync_workflow_templates(db_session)
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    # A session parent milestone keeps the feature from being parentless (no
    # synthetic milestone), so exactly one review — the feature's — is created.
    milestone = TicketService(db_session).create_ticket(
        workspace_slug="loregarden", title="Parent milestone", work_item_type=WorkItemType.MILESTONE
    )
    row = _session_row(db_session, workspace, _draft())
    row.parent_ticket_id = milestone.id
    db_session.add(row)
    db_session.commit()

    result = TicketStudioService(db_session).commit_session(row.id)

    reviews = [
        t
        for tid in result.created_ticket_ids
        if (t := db_session.get(Ticket, tid)) and t.is_integration_review
    ]
    assert len(reviews) == 1
    review = reviews[0]
    assert review.work_item_type == WorkItemType.CAPABILITY

    feature = _ticket_by_title(db_session, result.created_ticket_ids, "Markdown import")
    capability = _ticket_by_title(db_session, result.created_ticket_ids, "Parser")
    assert review.parent_ticket_id == feature.id
    assert TicketDependencyService(db_session).prerequisites(review.id) == [capability.id]
