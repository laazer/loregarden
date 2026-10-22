"""Has a reference repository changed since we reviewed it? Answers, and reasons."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.db.migration_utils import table_columns
from loregarden.db.versions.reference_survey_record import m_reference_survey_record
from loregarden.db.versions.survey_source_upstream import m_survey_source_upstream
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import SurveySourceKind
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_survey import upstream
from loregarden.services.reference_survey.upstream import check_upstream, refresh_stale
from sqlalchemy import create_engine, text
from sqlmodel import Session
from tests.survey_support import build_world, running_pass, write_tree

REPO = {"default_branch": "main", "pushed_at": "2026-10-20T10:00:00Z"}
HEAD = {"sha": "f" * 40, "commit": {"committer": {"date": "2026-10-20T09:30:00Z"}}}


def _gh(responses: dict[str, object]):
    """A fake `run_gh` answering `gh api <path>` from `responses`; a missing path fails."""

    def run(args, **_kwargs):
        path = args[1]
        for prefix, body in responses.items():
            if path.startswith(prefix):
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(body), stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="gh: Not Found (HTTP 404)")

    return run


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    source, _ = running_pass(session, world, write_tree(tmp_path / "clone", {"a.py": "a"}))
    yield session, world, source
    session.close()


def test_a_github_repo_reports_its_newest_commit_and_how_far_it_has_moved(ctx):
    session, _world, source = ctx
    responses = {
        "repos/acme/widget/compare/": {"ahead_by": 142, "status": "ahead"},
        "repos/acme/widget/commits/main": HEAD,
        "repos/acme/widget": REPO,
    }
    with mock.patch.object(upstream, "run_gh", side_effect=_gh(responses)):
        checked = check_upstream(session, source)
    assert (checked.upstream_branch, checked.upstream_head) == ("main", "f" * 40)
    assert checked.upstream_committed_at == datetime(2026, 10, 20, 9, 30)
    assert (checked.commits_since_review, checked.upstream_check_error) == (142, "")
    assert checked.upstream_checked_at is not None


def test_a_reviewed_commit_github_cannot_find_still_records_the_head(ctx):
    session, _world, source = ctx
    responses = {"repos/acme/widget/commits/main": HEAD, "repos/acme/widget": REPO}
    with mock.patch.object(upstream, "run_gh", side_effect=_gh(responses)):
        checked = check_upstream(session, source)
    assert checked.upstream_committed_at == datetime(2026, 10, 20, 9, 30)
    assert checked.commits_since_review is None
    assert "reviewed commit abc1234def could not be compared" in checked.upstream_check_error


def test_a_failed_check_says_why_and_is_not_an_empty_answer(ctx):
    session, _world, source = ctx
    with mock.patch.object(upstream, "run_gh", side_effect=_gh({})):
        checked = check_upstream(session, source)
    assert checked.upstream_committed_at is None
    assert "HTTP 404" in checked.upstream_check_error
    assert checked.upstream_checked_at is not None


def test_only_github_repositories_are_checked(ctx):
    session, world, _source = ctx
    gitlab = SurveySource(
        workspace_id=world.workspace_id,
        kind=SurveySourceKind.REPO,
        url="https://gitlab.com/a/b",
        name="b",
    )
    site = SurveySource(
        workspace_id=world.workspace_id,
        kind=SurveySourceKind.SITE,
        url="https://docs.x.dev/",
        name="x",
    )
    session.add_all([gitlab, site])
    session.commit()
    with mock.patch.object(upstream, "run_gh") as run_gh:
        assert (
            "Only GitHub repositories are checked"
            in check_upstream(session, gitlab).upstream_check_error
        )
        assert "has no commits to check" in check_upstream(session, site).upstream_check_error
    run_gh.assert_not_called()


def test_the_background_refresh_takes_the_longest_unchecked_first_and_skips_fresh_ones(ctx):
    session, world, source = ctx
    stale = SurveySource(
        workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url="https://github.com/acme/stale", name="stale",
        upstream_checked_at=utcnow() - timedelta(days=3),
    )  # fmt: skip
    fresh = SurveySource(
        workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url="https://github.com/acme/fresh", name="fresh",
        upstream_checked_at=utcnow() - timedelta(hours=1),
    )  # fmt: skip
    session.add_all([stale, fresh])
    session.commit()
    seen: list[str] = []
    with mock.patch.object(
        upstream, "check_upstream", side_effect=lambda _s, src: seen.append(src.name)
    ):
        assert refresh_stale(session, limit=5) == 2
    assert seen == [source.name, "stale"]


def test_the_api_checks_now_and_returns_the_row(client: TestClient, isolated_db, tmp_path):
    with Session(isolated_db) as session:
        world = build_world(session)
        source, _ = running_pass(session, world, write_tree(tmp_path / "api", {"a.py": "a"}))
        source_id = source.id
    responses = {
        "repos/acme/widget/compare/": {"ahead_by": 0, "status": "identical"},
        "repos/acme/widget/commits/main": HEAD,
        "repos/acme/widget": REPO,
    }
    with mock.patch.object(upstream, "run_gh", side_effect=_gh(responses)):
        response = client.post(f"/api/reference-library/{source_id}/check-upstream")
    assert response.status_code == 200, response.text
    assert (response.json()["commits_since_review"], response.json()["upstream_check_error"]) == (
        0,
        "",
    )
    assert client.post("/api/reference-library/missing/check-upstream").status_code == 404


def test_the_columns_are_added_to_the_shipped_survey_table_idempotently():
    """The survey record shipped first (20261021); the upstream columns follow it."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE workspaces (id VARCHAR PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE approvals (id VARCHAR PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE tickets (id VARCHAR PRIMARY KEY)"))
        m_reference_survey_record(conn)
        assert "upstream_checked_at" not in table_columns(conn, "survey_sources")
        m_survey_source_upstream(conn)
        m_survey_source_upstream(conn)
        added = {"upstream_branch", "upstream_head", "upstream_committed_at", "commits_since_review",
                 "upstream_checked_at", "upstream_check_error"}  # fmt: skip
        assert added <= set(table_columns(conn, "survey_sources"))
