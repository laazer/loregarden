"""Auto re-review: when a moved repository is queued again, what that pass reads,
and a person taking a source out."""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import (
    LicenceBasis,
    LicenceKind,
    LicenceUse,
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.reference_survey import feeder, upstream
from loregarden.services.reference_survey.coverage import map_tree
from loregarden.services.reference_survey.errors import SurveyError
from loregarden.services.reference_survey.passes import store_map
from loregarden.services.reference_survey.rereview import (
    RereviewSettingsIn,
    queue_due,
    save_settings,
    set_excluded,
    settings_view,
)
from sqlmodel import Session, select
from tests.survey_support import MIT_TEXT, build_world, serve, write_tree

PINNED = "a" * 40
NEW_HEAD = "c" * 40


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    tree = write_tree(
        tmp_path / "clone",
        {"LICENSE": MIT_TEXT, "src/a.py": "a", "docs/b.md": "b", "web/c.ts": "c"},
    )
    yield session, world, tree
    session.close()


def _reviewed(session, world, tree, name="widget", *, behind=40, checked_after_read=True):
    """A surveyed MIT repository the upstream check found `behind` commits ahead."""
    now = utcnow()
    source = SurveySource(
        workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url=f"https://github.com/acme/{name}",
        name=name, question="How do they gate?", queued_reason="r", status=SurveySourceStatus.DONE,
        licence_kind=LicenceKind.OPEN, licence_basis=LicenceBasis.FILE_AT_COMMIT, licence_use=LicenceUse.FULL,
        licence_name="MIT", licence_constraint="MIT: permissive.", pinned_commit=PINNED,
        fetched_at=now - timedelta(days=3), commits_since_review=behind,
        upstream_checked_at=now if checked_after_read else now - timedelta(days=4),
    )  # fmt: skip
    session.add(source)
    session.commit()
    serve(session, source, world.workspace_id)
    store_map(session, source, map_tree(tree), PINNED)
    session.refresh(source)
    return source


def _turn_on(session, *, threshold=25, cap=3):
    save_settings(
        session,
        RereviewSettingsIn(enabled=True, min_new_commits=threshold, max_commits_per_review=cap),
    )


def _rereviews(session, source):
    return session.exec(
        select(SurveyPass).where(
            SurveyPass.source_id == source.id, SurveyPass.mode == SurveyPassMode.REREVIEW
        )
    ).all()


def test_it_is_off_until_someone_turns_it_on(ctx):
    session, world, tree = ctx
    source = _reviewed(session, world, tree)
    assert settings_view(session).enabled is False
    assert queue_due(session) is None
    assert _rereviews(session, source) == []


def test_the_most_moved_repository_is_queued_and_only_one_at_a_time(ctx):
    session, world, tree = ctx
    less = _reviewed(session, world, tree, "less", behind=30)
    most = _reviewed(session, world, tree, "most", behind=900)
    _turn_on(session)
    queued = queue_due(session)
    assert queued is not None and queued.source_id == most.id
    assert (queued.mode, queued.status, queued.since_commit) == (
        SurveyPassMode.REREVIEW,
        SurveyPassStatus.QUEUED,
        PINNED,
    )
    session.refresh(most)
    assert most.status is SurveySourceStatus.ACTIVE
    assert queue_due(session) is None  # one in line already
    assert _rereviews(session, less) == []
    assert settings_view(session).in_flight.source_id == most.id


def test_below_the_threshold_or_counted_before_our_last_read_is_not_due(ctx):
    session, world, tree = ctx
    _reviewed(session, world, tree, "few", behind=10)
    _reviewed(session, world, tree, "stale", behind=500, checked_after_read=False)
    _turn_on(session, threshold=25)
    assert settings_view(session).due == 0
    assert queue_due(session) is None


def test_taking_a_source_out_withdraws_its_queued_rereview_and_it_is_never_due(ctx):
    session, world, tree = ctx
    source = _reviewed(session, world, tree)
    _turn_on(session)
    queued = queue_due(session)
    set_excluded(session, source.id, excluded=True)
    session.refresh(queued)
    session.refresh(source)
    assert queued.status is SurveyPassStatus.REFUSED
    assert "took this source out" in queued.refused_reason
    assert source.status is SurveySourceStatus.DONE
    assert queue_due(session) is None
    assert [e.id for e in settings_view(session).excluded] == [source.id]
    set_excluded(session, source.id, excluded=False)
    assert queue_due(session) is not None


def _gh(responses: dict[str, object]):
    def run(args, **_kwargs):
        path = args[1]
        for prefix, body in responses.items():
            if path.startswith(prefix):
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(body), stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="gh: Not Found (HTTP 404)")

    return run


def _commit(sha, subject):
    return {"sha": sha, "commit": {"message": f"{subject}\n\nbody"}}


def _dispatch(session, tree, responses):
    with (
        mock.patch.object(upstream, "run_gh", side_effect=_gh(responses)) as gh,
        mock.patch.object(
            feeder, "clone_or_refresh", return_value=(tree, "main", NEW_HEAD)
        ) as clone,
        mock.patch.object(feeder, "QueueLaneService"),
        mock.patch.object(feeder, "least_busy_lane", return_value=1),
    ):
        result = feeder.feed_once(session)
    return result, gh, clone


def test_a_rereview_reads_only_the_newest_commits_and_the_map_leaves_they_touched(ctx):
    session, world, tree = ctx
    source = _reviewed(session, world, tree, behind=40)
    _turn_on(session, cap=2)
    survey_pass = queue_due(session)
    page = [_commit(NEW_HEAD, "Gate on lease"), _commit("b" * 40, "Docs"), _commit("9" * 40, "Old")]
    responses = {
        "repos/acme/widget/commits?per_page=3": page,
        f"repos/acme/widget/compare/{'9' * 40}...{NEW_HEAD}": {
            "files": [
                {"filename": "src/a.py"},
                {"filename": "docs/b.md"},
                {"filename": "gone/x.txt"},
            ]
        },
    }
    result, gh, _clone = _dispatch(session, tree, responses)
    session.refresh(survey_pass)
    session.refresh(source)
    assert result.dispatched == survey_pass.id
    assert json.loads(survey_pass.slice_json) == ["docs/.", "src/."]
    assert "reading the newest 2 commits" in survey_pass.changes
    assert "38 older ones are not part of this pass" in survey_pass.changes
    assert "Gate on lease" in survey_pass.changes and "Old" not in survey_pass.changes
    assert source.pinned_commit == NEW_HEAD
    assert gh.call_count == 2


def test_a_window_that_reaches_the_reviewed_commit_compares_from_it(ctx):
    session, world, tree = ctx
    source = _reviewed(session, world, tree, behind=30)
    _turn_on(session, cap=5)
    survey_pass = queue_due(session)
    page = [_commit(NEW_HEAD, "Web"), _commit(PINNED, "Reviewed")]
    responses = {
        "repos/acme/widget/commits?per_page=6": page,
        f"repos/acme/widget/compare/{PINNED}...{NEW_HEAD}": {"files": [{"filename": "web/c.ts"}]},
    }
    _dispatch(session, tree, responses)
    session.refresh(survey_pass)
    assert json.loads(survey_pass.slice_json) == ["web/."]
    assert f"`{PINNED[:12]}...{NEW_HEAD[:12]}`" in survey_pass.changes
    assert source.id == survey_pass.source_id


def test_github_failing_refuses_before_the_clone_and_retries_the_range_a_day_later(ctx):
    session, world, tree = ctx
    source = _reviewed(session, world, tree)
    _turn_on(session)
    survey_pass = queue_due(session)
    result, _gh_mock, clone = _dispatch(session, tree, {})
    session.refresh(survey_pass)
    session.refresh(source)
    assert result.refused == survey_pass.id
    assert "HTTP 404" in survey_pass.refused_reason
    clone.assert_not_called()
    assert (source.pinned_commit, source.status) == (PINNED, SurveySourceStatus.DONE)
    assert queue_due(session) is None  # not every tick
    survey_pass.finished_at = utcnow() - timedelta(days=2)
    session.add(survey_pass)
    session.commit()
    assert queue_due(session) is not None


def test_commits_touching_nothing_in_the_map_refuse_with_the_reason(ctx):
    session, world, tree = ctx
    _reviewed(session, world, tree)
    _turn_on(session, cap=5)
    survey_pass = queue_due(session)
    responses = {
        "repos/acme/widget/commits": [_commit(NEW_HEAD, "Remove"), _commit(PINNED, "Reviewed")],
        "repos/acme/widget/compare/": {"files": [{"filename": "deleted/x.py"}]},
    }
    _dispatch(session, tree, responses)
    session.refresh(survey_pass)
    assert survey_pass.status is SurveyPassStatus.REFUSED
    assert "touched no file in the map" in survey_pass.refused_reason


def test_the_api_reads_saves_and_bounds_the_settings_and_takes_a_source_out(
    client: TestClient, isolated_db, tmp_path
):
    with Session(isolated_db) as session:
        world = build_world(session)
        source_id = _reviewed(session, world, write_tree(tmp_path / "t", {"a.py": "a"})).id
    got = client.get("/api/reference-library/auto-rereview").json()
    assert (got["enabled"], got["min_new_commits"], got["max_commits_per_review"]) == (
        False,
        25,
        50,
    )
    assert got["due"] == 1
    saved = client.put(
        "/api/reference-library/auto-rereview",
        json={"enabled": True, "min_new_commits": 100, "max_commits_per_review": 20},
    ).json()
    assert (saved["enabled"], saved["due"]) == (True, 0)
    too_many = client.put(
        "/api/reference-library/auto-rereview",
        json={"enabled": True, "min_new_commits": 1, "max_commits_per_review": 101},
    )
    assert too_many.status_code == 422
    row = client.put(f"/api/reference-library/{source_id}/auto-rereview", json={"excluded": True})
    assert row.json()["auto_rereview_excluded"] is True
    missing = client.put("/api/reference-library/nope/auto-rereview", json={"excluded": True})
    assert missing.status_code == 404


def test_an_agent_takes_a_source_out_through_the_queue_tool(ctx):
    session, world, tree = ctx
    source = _reviewed(session, world, tree)
    raw = execute_tool(
        session,
        "loregarden_survey_queue",
        {"action": "set_auto_rereview", "source_id": source.id, "excluded": True},
        orchestrated=True,
        run_id="",
    )
    assert json.loads(raw)["auto_rereview_excluded"] is True
    with pytest.raises(SurveyError, match="needs `excluded`"):
        execute_tool(
            session,
            "loregarden_survey_queue",
            {"action": "set_auto_rereview", "source_id": source.id},
            orchestrated=True,
            run_id="",
        )
