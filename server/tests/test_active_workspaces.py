"""A sweep over every workspace skips the archived ones.

Three workspaces a test run left in the live database (temp-directory repos,
2026-10-11 and 2026-10-17) kept appearing in the chat branch sweep and the
workflow monitor after they were archived, because those two loops read
every `Workspace` row while the CI poll and integration health skipped
archived ones. Every such loop now asks `active_workspaces`.
"""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.models.domain import Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.services import chat_branch_sweep, ci_poll_scheduler, workflow_monitor
from loregarden.services.active_workspaces import active_workspaces
from sqlmodel import Session


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="live_and_archived")
def live_and_archived_fixture(session, tmp_path):
    live = Workspace(slug="live", name="Live", repo_path=str(tmp_path / "live"))
    archived = Workspace(
        slug="gone", name="Gone", repo_path=str(tmp_path / "gone"), archived_at=utcnow()
    )
    session.add_all([live, archived])
    session.commit()
    return live, archived


def test_only_the_unarchived_are_active(session, live_and_archived):
    assert [ws.slug for ws in active_workspaces(session)] == ["live"]


def test_the_chat_branch_sweep_skips_an_archived_workspace(session, live_and_archived):
    swept = mock.Mock(return_value=chat_branch_sweep.ChatBranchSweep())
    with mock.patch.object(chat_branch_sweep, "sweep_chat_branches", swept):
        chat_branch_sweep.sweep_all_chat_branches(session)

    assert [call.args[1].slug for call in swept.call_args_list] == ["live"]


def test_the_timeout_floor_monitor_skips_an_archived_workspace(session, live_and_archived):
    profiles = mock.Mock(return_value={})
    with mock.patch.object(workflow_monitor, "load_stage_duration_profiles", profiles):
        workflow_monitor._detect_timeout_floor_stale(session)

    live, _ = live_and_archived
    assert {call.kwargs["workspace_id"] for call in profiles.call_args_list} == {live.id}


def test_the_ci_poll_skips_an_archived_workspace(session, live_and_archived):
    polled = mock.Mock(return_value=ci_poll_scheduler.WorkspacePoll(slug="live"))
    with (
        mock.patch.object(ci_poll_scheduler, "poll_workspace", polled),
        mock.patch.object(ci_poll_scheduler.CIService, "prune_logs"),
    ):
        ci_poll_scheduler.run_ci_poll(session)

    assert [call.args[1].slug for call in polled.call_args_list] == ["live"]
