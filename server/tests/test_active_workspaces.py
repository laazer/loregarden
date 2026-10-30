"""A sweep over every workspace skips the archived ones.

Three workspaces a test run left in the live database (temp-directory repos,
2026-10-11 and 2026-10-17) kept appearing in the chat branch sweep and the
workflow monitor after they were archived, because those two loops read
every `Workspace` row while the CI poll and integration health skipped
archived ones. Every such loop now asks `active_workspaces`.

The studio generation tests at the bottom cover the last such selection,
`studio_generation._default_studio_workspace`, which chose a workspace to run
the generate model in without asking whether it was archived.
"""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.models.domain import Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.services import (
    chat_branch_sweep,
    ci_poll_scheduler,
    studio_generation,
    workflow_monitor,
)
from loregarden.services.active_workspaces import active_workspaces
from loregarden.services.studio_generation import _default_studio_workspace
from sqlmodel import Session, select


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


# --- The studio generation selection (AC5, AC6) -------------------------------
#
# `_default_studio_workspace` was the one remaining selection over every
# `Workspace` row that then acts on one: the slug lookup and the order_by
# fallback both ran unfiltered, so an archived workspace could be handed to
# studio generation and the failure arm was unreachable behind an archived
# `loregarden` row.

#: The exact `detail` a 400 from either studio generate route carries. Pinned
#: here rather than imported, because the cause-and-remedy wording IS the
#: criterion; importing it from production would assert nothing.
NO_ACTIVE_WORKSPACE_DETAIL = (
    "No active workspace for studio generation: every workspace is archived, or none "
    "exists. Restore one or add one on the Workspaces page, then generate again."
)


def _add_workspace(session: Session, slug: str, tmp_path, *, archived: bool) -> Workspace:
    workspace = Workspace(
        slug=slug,
        name=slug,
        repo_path=str(tmp_path / slug),
        archived_at=utcnow() if archived else None,
    )
    session.add(workspace)
    session.commit()
    return workspace


def test_studio_generation_prefers_the_active_loregarden_workspace(session, tmp_path):
    _add_workspace(session, "loregarden", tmp_path, archived=False)
    _add_workspace(session, "zz-other", tmp_path, archived=False)

    assert _default_studio_workspace(session).slug == "loregarden"


def test_studio_generation_skips_an_archived_loregarden_workspace(session, tmp_path):
    """The slug lookup is filtered too, not only the fallback: an archived
    `loregarden` row would otherwise be returned and the failure never reached."""
    _add_workspace(session, "loregarden", tmp_path, archived=True)
    _add_workspace(session, "zz-other", tmp_path, archived=False)

    assert _default_studio_workspace(session).slug == "zz-other"


def test_studio_generation_raises_when_every_workspace_is_archived(session, tmp_path):
    _add_workspace(session, "loregarden", tmp_path, archived=True)

    with pytest.raises(ValueError, match="No active workspace for studio generation"):
        _default_studio_workspace(session)


def test_the_studio_generate_route_names_the_cause_and_the_remedy(client, db_session, monkeypatch):
    """The `ValueError` surfaces as the 400 `detail`, which `StudioDescribeBar`
    renders verbatim, so the message a person reads is this string."""
    # The stub short-circuits before the workspace is resolved; the request has
    # to reach `_default_studio_workspace`, so it must not be configured.
    monkeypatch.delenv("LOREGARDEN_STUDIO_GENERATE_STUB_RESPONSE", raising=False)
    for workspace in db_session.exec(select(Workspace)).all():
        workspace.archived_at = utcnow()
        db_session.add(workspace)
    db_session.commit()

    response = client.post(
        "/api/studio/agents/generate",
        json={"description": "An agent that reviews localization changes"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == NO_ACTIVE_WORKSPACE_DETAIL


def test_studio_generation_prefers_loregarden_over_an_alphabetically_earlier_slug(
    session, tmp_path
):
    """The slug preference survives the filter.

    `test_studio_generation_prefers_the_active_loregarden_workspace` pairs
    `loregarden` with `zz-other`, and `loregarden` sorts first, so filtering
    both arms while dropping the slug lookup entirely would still pass it.
    Here the other active workspace sorts ahead of `loregarden`, so only a
    selection that actually prefers the slug returns it.
    """
    _add_workspace(session, "aa-first", tmp_path, archived=False)
    _add_workspace(session, "loregarden", tmp_path, archived=False)

    assert _default_studio_workspace(session).slug == "loregarden"


def test_studio_generation_falls_back_to_the_first_active_slug_not_the_first_row(session, tmp_path):
    """ "Otherwise the first active workspace by slug" is an ordering, not an
    insertion order: the later-sorting row is written first on purpose, so a
    fallback that filters but forgets `order_by` returns the wrong one."""
    _add_workspace(session, "zz-written-first", tmp_path, archived=False)
    _add_workspace(session, "aa-written-second", tmp_path, archived=False)

    assert _default_studio_workspace(session).slug == "aa-written-second"


def test_studio_generation_raises_when_there_is_no_workspace_at_all(session, tmp_path):
    """The other half of AC6's "every workspace is archived, or none exists":
    an empty table reaches the same message, not a different failure."""
    for workspace in session.exec(select(Workspace)).all():
        session.delete(workspace)
    session.commit()

    with pytest.raises(ValueError, match="No active workspace for studio generation"):
        _default_studio_workspace(session)


@pytest.mark.parametrize("route", ["/api/studio/agents/generate", "/api/studio/workflows/generate"])
def test_both_studio_generate_routes_name_the_cause_and_the_remedy(
    client, db_session, monkeypatch, route
):
    """The workflow route carries the same detail as the agent route, and
    neither reaches the model: the generate turn must not be handed an
    archived workspace, so a call to the runner is itself the failure."""
    monkeypatch.delenv("LOREGARDEN_STUDIO_GENERATE_STUB_RESPONSE", raising=False)
    for workspace in db_session.exec(select(Workspace)).all():
        workspace.archived_at = utcnow()
        db_session.add(workspace)
    db_session.commit()

    def never(*args, **kwargs):
        raise AssertionError("the generate turn was handed a workspace, all of them archived")

    monkeypatch.setattr(studio_generation, "run_cli_agent_turn", never)

    response = client.post(route, json={"description": "A reviewer of localization changes"})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == NO_ACTIVE_WORKSPACE_DETAIL
