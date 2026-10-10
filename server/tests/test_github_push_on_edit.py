"""Push-on-edit: a linked ticket's edits reach its issue without a manual sync."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    GithubIssueLink,
    Ticket,
    TicketState,
    UpdateGithubSyncSettings,
    UpdateTicketRequest,
    WorkItemType,
    Workspace,
)
from loregarden.services.github_issue_sync import link_for_ticket, publish_ticket, sync_link
from loregarden.services.github_push_on_edit import edit_push_queue, process_due_pushes
from loregarden.services.github_sync_scheduler import update_sync_settings
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select
from tests.github_fake import FakeGh

#: Far enough ahead that every debounce has elapsed.
LATER = 1e12


@pytest.fixture(name="gh")
def gh_fixture():
    fake = FakeGh()
    with patch("loregarden.services.github_issue_client.run_gh", side_effect=fake):
        yield fake


@pytest.fixture(name="accepting", autouse=True)
def accepting_fixture():
    """What `start_push_worker` does, without the asyncio task."""
    edit_push_queue.clear()
    edit_push_queue.accepting = True
    yield edit_push_queue
    edit_push_queue.accepting = False
    edit_push_queue.clear()


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session) -> Workspace:
    return db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, workspace: Workspace) -> Ticket:
    milestone = TicketService(db_session).create_ticket(
        workspace_slug=workspace.slug, title="Inbox", work_item_type=WorkItemType.MILESTONE
    )
    return TicketService(db_session).create_ticket(
        workspace_slug=workspace.slug,
        title="Widgets wobble",
        description="They wobble.",
        work_item_type=WorkItemType.BUG,
        parent_ticket_id=milestone.id,
    )


@pytest.fixture(name="linked")
def linked_fixture(db_session: Session, gh: FakeGh, ticket: Ticket) -> GithubIssueLink:
    publish_ticket(db_session, ticket)
    link = link_for_ticket(db_session, ticket.id)
    assert link is not None
    edit_push_queue.clear()
    return link


def _push_on_edit(session: Session, workspace: Workspace, on: bool = True) -> None:
    update_sync_settings(
        session, workspace, UpdateGithubSyncSettings(enabled=False, push_on_edit=on)
    )


def _edit(session: Session, ticket: Ticket, **fields) -> None:
    OrchestrationService(session).update_ticket_manual(ticket, UpdateTicketRequest(**fields))


def test_an_edit_is_pushed_after_the_debounce(
    db_session: Session, gh: FakeGh, workspace: Workspace, ticket: Ticket, linked
):
    _push_on_edit(db_session, workspace)
    _edit(db_session, ticket, title="Widgets wobble badly")

    assert edit_push_queue.pending() == {ticket.id}
    assert process_due_pushes(now=0) == {}  # not yet due
    assert process_due_pushes(now=LATER) == {ticket.id: ""}
    assert gh.issues[linked.issue_number]["title"] == "Widgets wobble badly"


def test_closing_the_ticket_closes_the_issue(
    db_session: Session, gh: FakeGh, workspace: Workspace, ticket: Ticket, linked
):
    _push_on_edit(db_session, workspace)
    _edit(db_session, ticket, state=TicketState.DONE)

    process_due_pushes(now=LATER)

    assert gh.issues[linked.issue_number]["state"] == "CLOSED"


def test_off_by_default(db_session: Session, gh: FakeGh, ticket: Ticket, linked):
    _edit(db_session, ticket, title="Renamed")
    gh.calls.clear()

    assert process_due_pushes(now=LATER) == {}
    assert gh.calls == []


def test_unlinked_and_unmirrored_edits_do_nothing(
    db_session: Session, gh: FakeGh, workspace: Workspace, ticket: Ticket
):
    _push_on_edit(db_session, workspace)
    _edit(db_session, ticket, title="Not linked")
    assert process_due_pushes(now=LATER) == {}

    publish_ticket(db_session, ticket)
    edit_push_queue.clear()
    _edit(db_session, ticket, priority=1)
    assert edit_push_queue.pending() == set()


def test_edits_pulled_from_github_are_not_pushed_back(
    db_session: Session, gh: FakeGh, workspace: Workspace, ticket: Ticket, linked
):
    _push_on_edit(db_session, workspace)
    gh.issues[linked.issue_number]["title"] = "Edited on GitHub"

    sync_link(db_session, linked)

    db_session.expire_all()
    assert db_session.get(Ticket, ticket.id).title == "Edited on GitHub"
    assert edit_push_queue.pending() == set()


def test_a_rolled_back_edit_is_not_queued(db_session: Session, ticket: Ticket, linked):
    ticket.title = "Never committed"
    db_session.add(ticket)
    db_session.flush()
    db_session.rollback()

    assert edit_push_queue.pending() == set()


def test_a_failed_push_is_reported_and_recorded(
    db_session: Session, gh: FakeGh, workspace: Workspace, ticket: Ticket, linked
):
    _push_on_edit(db_session, workspace)
    _edit(db_session, ticket, title="Pushed later")
    gh.fail_verb = "edit"

    outcome = process_due_pushes(now=LATER)

    assert "failed" in outcome[ticket.id]
    db_session.expire_all()
    assert "failed" in db_session.get(GithubIssueLink, linked.id).last_error


def test_nothing_is_collected_without_a_worker(db_session: Session, ticket: Ticket, linked):
    edit_push_queue.accepting = False
    _edit(db_session, ticket, title="Nobody drains this")

    assert edit_push_queue.pending() == set()


def test_rest_settings_carry_push_on_edit(client):
    url = "/api/workspaces/loregarden/github-issues/settings"
    assert client.get(url).json()["push_on_edit"] is False
    client.put(url, json={"enabled": False, "push_on_edit": True})
    assert client.get(url).json()["push_on_edit"] is True
