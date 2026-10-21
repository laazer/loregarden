"""A workspace on a temp-directory repo is refused in the live database, by every writer.

lg-milestone-that-1319: verifying 779 and 929 left `surface-ws`, `real929` and
`real929b` in the live database, on throwaway `tempfile` repos. Each test here
treats its own `isolated_db` as the live database by patching
`live_database_path`, so the guard is exercised exactly as it runs live.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from loregarden.db import workspace_repo_guard
from loregarden.db.workspace_repo_guard import TempRepoInLiveDatabaseError
from loregarden.models.domain import Workspace
from loregarden.services.invariant_registry import LIVE_WORKSPACES_HAVE_REAL_REPOS
from loregarden.services.invariants import run_invariant
from loregarden.testing.factories import make_workspace
from sqlmodel import Session, select


@pytest.fixture(name="as_live")
def as_live_fixture(isolated_db):
    """This test's database stands in for the primary checkout's."""
    live = Path(isolated_db.url.database).resolve()
    with mock.patch.object(workspace_repo_guard, "live_database_path", return_value=live):
        yield


@pytest.fixture(name="temp_repo")
def temp_repo_fixture(tmp_path) -> str:
    repo = tmp_path / "probe" / "repo"
    repo.mkdir(parents=True)
    return str(repo)


def _write_orm(session: Session, repo: str) -> None:
    session.add(Workspace(slug="probe-orm", name="probe", repo_path=repo))
    session.commit()


def _write_factory(session: Session, repo: str) -> None:
    make_workspace(session, slug="probe-factory", repo_path=repo)


def _move_onto(session: Session, repo: str) -> None:
    workspace = Workspace(slug="probe-moved", name="probe", repo_path=".")
    session.add(workspace)
    session.commit()
    workspace.repo_path = repo
    session.add(workspace)
    session.commit()


@pytest.mark.parametrize(
    "write", [_write_orm, _write_factory, _move_onto], ids=lambda w: w.__name__
)
def test_every_writer_of_a_temp_repo_to_the_live_database_is_refused(
    client, isolated_db, as_live, temp_repo, write
):
    """The ORM, the test factories and an update that moves a row onto one; the
    API, which every other surface reaches the service through, answers 400."""
    with Session(isolated_db) as session, pytest.raises(TempRepoInLiveDatabaseError):
        write(session, temp_repo)

    response = client.post(
        "/api/workspaces", json={"slug": "probe-api", "name": "probe", "repo_path": temp_repo}
    )
    assert response.status_code == 400, response.text
    assert "temp directory" in response.json()["detail"]
    with Session(isolated_db) as session:
        slugs = set(session.exec(select(Workspace.slug)).all())
    assert not slugs & {"probe-orm", "probe-factory", "probe-api"}


def test_any_other_database_takes_a_temp_repo(isolated_db, temp_repo):
    """A test's, a sandbox's or a scenario's database is its own."""
    with Session(isolated_db) as session:
        _write_orm(session, temp_repo)
        assert session.exec(select(Workspace).where(Workspace.slug == "probe-orm")).one()


def test_a_real_repo_in_the_live_database_is_written(isolated_db, as_live, tmp_path):
    with Session(isolated_db) as session:
        session.add(Workspace(slug="real", name="real", repo_path="."))
        session.commit()


def test_the_invariant_archives_a_temp_workspace_that_predates_the_guard(
    client, isolated_db, as_live, temp_repo
):
    with Session(isolated_db) as session:
        with mock.patch.object(workspace_repo_guard, "_check"):
            _write_orm(session, temp_repo)  # as surface-ws was, before the guard

        check = run_invariant(session, LIVE_WORKSPACES_HAVE_REAL_REPOS)

        # The seeded `loregarden` workspace is on a pytest temp repo too; both go.
        assert check.found == check.repaired >= 1
        workspace = session.exec(select(Workspace).where(Workspace.slug == "probe-orm")).one()
        assert workspace.archived_at is not None, "archiving it is never refused"
        assert list(LIVE_WORKSPACES_HAVE_REAL_REPOS.detect(session)) == []


def test_the_invariant_leaves_other_databases_alone(isolated_db, temp_repo):
    with Session(isolated_db) as session:
        _write_orm(session, temp_repo)

        assert list(LIVE_WORKSPACES_HAVE_REAL_REPOS.detect(session)) == []
