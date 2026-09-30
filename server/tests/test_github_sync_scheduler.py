"""Background GitHub issue sync: off by default, per workspace, never silent."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    GithubSyncSettings,
    LinkSyncResult,
    Ticket,
    UpdateGithubSyncSettings,
    WorkItemType,
    Workspace,
    WorkspaceSyncResult,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.github_issue_client import GithubIssueError
from loregarden.services.github_sync_scheduler import (
    get_sync_settings,
    is_due,
    run_due_syncs,
    start_github_sync_loop,
    update_sync_settings,
)
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select

SYNC = "loregarden.services.github_sync_scheduler.sync_workspace"


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session) -> Workspace:
    return db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


@pytest.fixture(name="milestone")
def milestone_fixture(db_session: Session, workspace: Workspace) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug=workspace.slug, title="Inbox", work_item_type=WorkItemType.MILESTONE
    )


def _enable(session: Session, workspace: Workspace, **over) -> None:
    body = {"enabled": True, "interval_minutes": 15, **over}
    update_sync_settings(session, workspace, UpdateGithubSyncSettings(**body))


def _ok(slug: str = "loregarden") -> WorkspaceSyncResult:
    return WorkspaceSyncResult(workspace_slug=slug, repo="acme/widgets")


def test_off_by_default(db_session: Session, workspace: Workspace):
    assert get_sync_settings(db_session, workspace).enabled is False
    with patch(SYNC) as sync:
        assert run_due_syncs(db_session) == {}
    sync.assert_not_called()


def test_an_enabled_workspace_runs_then_waits_its_interval(
    db_session: Session, workspace: Workspace
):
    _enable(db_session, workspace)
    now = utcnow()
    with patch(SYNC, return_value=_ok()) as sync:
        assert run_due_syncs(db_session, now) == {"loregarden": ""}
        assert run_due_syncs(db_session, now + timedelta(minutes=14)) == {}
        assert run_due_syncs(db_session, now + timedelta(minutes=15)) == {"loregarden": ""}
    assert sync.call_count == 2


def test_the_saved_import_parent_and_label_are_used(
    db_session: Session, workspace: Workspace, milestone: Ticket
):
    _enable(db_session, workspace, import_parent_ticket_id=milestone.id, import_label=" triage ")
    with patch(SYNC, return_value=_ok()) as sync:
        run_due_syncs(db_session)
    assert sync.call_args.kwargs == {
        "import_parent_ticket_id": milestone.id,
        "import_label": "triage",
    }


def test_a_parent_outside_the_workspace_is_refused(db_session: Session, workspace: Workspace):
    with pytest.raises(ValueError, match="import parent"):
        _enable(db_session, workspace, import_parent_ticket_id="no-such-ticket")


def test_disabling_stops_runs(db_session: Session, workspace: Workspace):
    _enable(db_session, workspace)
    update_sync_settings(db_session, workspace, UpdateGithubSyncSettings(enabled=False))
    with patch(SYNC) as sync:
        assert run_due_syncs(db_session) == {}
    sync.assert_not_called()


def test_a_failed_run_is_recorded_and_retried_next_interval(
    db_session: Session, workspace: Workspace
):
    _enable(db_session, workspace)
    now = utcnow()
    with patch(SYNC, side_effect=GithubIssueError("gh: not logged in")):
        assert run_due_syncs(db_session, now) == {"loregarden": "gh: not logged in"}
    view = get_sync_settings(db_session, workspace)
    assert view.last_error == "gh: not logged in"
    assert view.last_run_at is not None
    with patch(SYNC, return_value=_ok()):
        run_due_syncs(db_session, now + timedelta(minutes=15))
    assert get_sync_settings(db_session, workspace).last_error == ""


def test_failed_links_are_summarised_on_the_row(db_session: Session, workspace: Workspace):
    _enable(db_session, workspace)
    result = _ok()
    result.links = [
        LinkSyncResult(ticket_id="a", external_id="lor-1", issue_number=1, issue_url=""),
        LinkSyncResult(
            ticket_id="b", external_id="lor-2", issue_number=2, issue_url="", error="HTTP 404"
        ),
    ]
    with patch(SYNC, return_value=result):
        run_due_syncs(db_session)
    error = get_sync_settings(db_session, workspace).last_error
    assert error.startswith("1 of 2 linked tickets failed")
    assert "lor-2" in error and "HTTP 404" in error


def test_is_due_reads_a_naive_timestamp_as_utc():
    now = utcnow()
    row = GithubSyncSettings(
        workspace_id="w",
        enabled=True,
        interval_minutes=5,
        last_run_at=(now - timedelta(minutes=6)).replace(tzinfo=None),
    )
    assert is_due(row, now)


def test_a_zero_tick_disables_the_scheduler():
    assert start_github_sync_loop(0) is None


def test_rest_settings_round_trip(client, milestone: Ticket):
    url = "/api/workspaces/loregarden/github-issues/settings"
    assert client.get(url).json()["enabled"] is False

    saved = client.put(
        url,
        json={"enabled": True, "interval_minutes": 30, "import_parent_ticket_id": milestone.id},
    )
    assert saved.status_code == 200, saved.text
    assert client.get(url).json()["interval_minutes"] == 30
    assert client.put(url, json={"enabled": True, "interval_minutes": 1}).status_code == 422
    assert client.get("/api/workspaces/nope/github-issues/settings").status_code == 404
