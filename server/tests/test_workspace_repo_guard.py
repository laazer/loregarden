"""A workspace on a temp-directory repo is refused in the live database, by every writer.

lg-milestone-that-1319: verifying 779 and 929 left `surface-ws`, `real929` and
`real929b` in the live database, on throwaway `tempfile` repos. Each test here
treats its own `isolated_db` as the live database by patching
`live_database_path`, so the guard is exercised exactly as it runs live.

That fixture proves the wiring and not the aim: it patches away the one
question the guard asks, which database it is writing to. The tests at the
bottom of this module patch one seam lower instead — `main_working_tree`, the
answer `live_database_path` is built from — so `live_database_path` and the
file comparison inside `writes_live_database` run for real.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.db import workspace_repo_guard
from loregarden.db.workspace_repo_guard import TempRepoInLiveDatabaseError
from loregarden.models.domain import Workspace
from loregarden.services.invariant_registry import LIVE_WORKSPACES_HAVE_REAL_REPOS
from loregarden.services.invariants import run_invariant
from loregarden.testing.factories import make_workspace
from sqlalchemy import create_engine
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


# --- The predicates the fixture above patches out (AC7) -----------------------
#
# `as_live` replaces `live_database_path`, so no test above it executes
# `main_working_tree()`, the `@cache`, the "git could not answer" fallback, or
# the `Path(database).resolve() == live_database_path()` comparison. AC1 and
# AC2 both turn on exactly that distinction, so it is exercised here, with
# `main_working_tree` patched rather than the predicate under test.


@pytest.fixture(name="uncached_live_path")
def uncached_live_path_fixture():
    """`live_database_path` is cached; nothing may inherit a patched answer.

    Cleared on the way in so the first call under the patch computes, and on
    the way out so the next test (and the reconcile timer, which shares the
    process) recomputes the real one.
    """
    workspace_repo_guard.live_database_path.cache_clear()
    try:
        yield
    finally:
        workspace_repo_guard.live_database_path.cache_clear()


def test_the_live_database_is_the_primary_checkouts(uncached_live_path, tmp_path):
    """What every checkout shares: the primary working tree's own database."""
    primary = tmp_path / "primary"
    (primary / "data").mkdir(parents=True)

    with mock.patch.object(workspace_repo_guard, "main_working_tree", return_value=primary):
        workspace_repo_guard.live_database_path.cache_clear()

        assert (
            workspace_repo_guard.live_database_path()
            == (primary / "data" / "loregarden.db").resolve()
        )


def test_this_checkouts_database_is_taken_when_git_cannot_answer(uncached_live_path):
    """`main_working_tree` returning None is "git could not be asked", never
    "there is no main working tree", so the guard protects this checkout's own."""
    with mock.patch.object(workspace_repo_guard, "main_working_tree", return_value=None):
        workspace_repo_guard.live_database_path.cache_clear()

        assert (
            workspace_repo_guard.live_database_path()
            == (settings.repo_root / "data" / "loregarden.db").resolve()
        )


@pytest.mark.parametrize("is_the_live_file", [True, False], ids=["the live file", "another file"])
def test_only_a_connection_to_the_live_file_writes_the_live_database(
    uncached_live_path, tmp_path, is_the_live_file
):
    """The comparison `as_live` stands in for, against a real engine's URL."""
    primary = tmp_path / "primary"
    (primary / "data").mkdir(parents=True)
    database = (primary / "data" / "loregarden.db") if is_the_live_file else (tmp_path / "other.db")

    with mock.patch.object(workspace_repo_guard, "main_working_tree", return_value=primary):
        workspace_repo_guard.live_database_path.cache_clear()
        engine = create_engine(f"sqlite:///{database}")
        try:
            with engine.connect() as connection:
                assert workspace_repo_guard.writes_live_database(connection) is is_the_live_file
        finally:
            engine.dispose()


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "sqlite://"], ids=[":memory:", "no file"])
def test_a_database_with_no_file_is_never_the_live_one(url):
    """`:memory:` and a URL naming no database at all have no file to compare,
    so they are somebody's fixture and take whatever they are given."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert workspace_repo_guard.writes_live_database(connection) is False
    finally:
        engine.dispose()


def test_the_live_database_is_resolved_through_a_symlinked_checkout(uncached_live_path, tmp_path):
    """Two names for one file are one database.

    `/tmp` is a symlink to `/private/tmp` on macOS, and a checkout reached
    through a symlink is the same checkout, so the live path is the resolved
    file rather than the name it was reached by. Without that, a process
    holding the other name writes the live database unguarded.
    """
    primary = tmp_path / "primary"
    (primary / "data").mkdir(parents=True)
    through_a_link = tmp_path / "by-another-name"
    through_a_link.symlink_to(primary, target_is_directory=True)

    with mock.patch.object(workspace_repo_guard, "main_working_tree", return_value=through_a_link):
        workspace_repo_guard.live_database_path.cache_clear()

        assert (
            workspace_repo_guard.live_database_path()
            == (primary / "data" / "loregarden.db").resolve()
        )


def test_a_connection_reaching_the_live_file_by_another_name_writes_it(
    uncached_live_path, tmp_path
):
    """The same, from the connection's side: the engine's URL is the name the
    caller happened to use, so it is compared resolved."""
    primary = tmp_path / "primary"
    (primary / "data").mkdir(parents=True)
    through_a_link = tmp_path / "by-another-name"
    through_a_link.symlink_to(primary, target_is_directory=True)

    with mock.patch.object(workspace_repo_guard, "main_working_tree", return_value=primary):
        workspace_repo_guard.live_database_path.cache_clear()
        engine = create_engine(f"sqlite:///{through_a_link / 'data' / 'loregarden.db'}")
        try:
            with engine.connect() as connection:
                assert workspace_repo_guard.writes_live_database(connection) is True
        finally:
            engine.dispose()


def _temp_root() -> Path:
    return Path(tempfile.gettempdir()).resolve()


@pytest.mark.parametrize(
    ("repo_path", "is_temp"),
    [
        pytest.param(lambda: str(_temp_root()), True, id="the temp root itself"),
        pytest.param(lambda: str(_temp_root() / "probe" / "repo"), True, id="inside it"),
        pytest.param(
            lambda: f"{_temp_root()}-not-mine/repo",
            False,
            id="a sibling whose name starts with it",
        ),
        pytest.param(lambda: "tmp/repo", False, id="relative"),
        pytest.param(lambda: f"../{_temp_root().name}/repo", False, id="relative via .."),
        pytest.param(lambda: str(settings.repo_root), False, id="a real checkout"),
    ],
)
def test_what_counts_as_a_temp_directory_repo(repo_path, is_temp):
    """The boundary AC1 turns on. A prefix comparison would call
    `/private/var/folders/..T-not-mine` a temp repo, and a path never resolved
    would miss `..`; neither is a string the guard may get wrong, because one
    refuses a real workspace and the other admits a probe's."""
    assert workspace_repo_guard.is_temp_repo(repo_path()) is is_temp
