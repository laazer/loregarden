"""Refuse a workspace on a temp-directory repo in the live database.

Verifying lg-milestone-that-779 and -929 left three workspaces in the live
database (`surface-ws`, `real929`, `real929b`) whose repos were throwaway
`tempfile` directories: a probe built its fixture there and wrote the rows
through whatever database the process resolved, which was the live one. They
stayed in every sweep over workspaces and in the server log
(lg-milestone-that-1319).

A temp directory is a test's or a probe's, never a project's, so the live
database refuses one. Any other database (each test's `isolated_db`, a `task
sandbox` copy, a seeded scenario) is a fixture's own, and takes whatever it
is given.

Registered on the `Workspace` mapper, like `workflow_pin_guard`, so the API,
the MCP tools, the CLI, the test factories and a raw ORM write all meet it and
a writer added later inherits it. An update is checked only when it sets
`repo_path`, so archiving one of these, which is the repair, is never refused.
"""

from __future__ import annotations

import tempfile
from functools import cache
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.git_trees import main_working_tree
from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper


class TempRepoInLiveDatabaseError(ValueError):
    """A workspace on a temp-directory repo was written to the live database."""


@cache
def live_database_path() -> Path:
    """The primary checkout's own database: what every checkout of it shares.

    When git cannot say which checkout is primary, this checkout's own is taken.
    In the primary checkout that is still the live one; in a worktree whose git
    cannot be read, the guard then protects that worktree's database instead.
    """
    primary = main_working_tree(settings.repo_root) or settings.repo_root
    return (primary / "data" / "loregarden.db").resolve()


@cache
def _temp_roots() -> tuple[Path, ...]:
    return tuple({Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()})


def writes_live_database(connection: Connection) -> bool:
    """Whether ``connection`` is to the primary checkout's live database."""
    database = connection.engine.url.database
    if not database or database == ":memory:":
        return False
    return Path(database).resolve() == live_database_path()


def is_temp_repo(repo_path: str) -> bool:
    """Whether ``repo_path`` is an absolute path inside a temp directory."""
    path = Path(repo_path).expanduser()
    if not path.is_absolute():
        return False
    resolved = path.resolve()
    return any(resolved.is_relative_to(root) for root in _temp_roots())


def _check(target: Workspace, connection: Connection) -> None:
    if not is_temp_repo(target.repo_path) or not writes_live_database(connection):
        return
    raise TempRepoInLiveDatabaseError(
        f"Refusing to write workspace {target.slug!r} on {target.repo_path} to the live "
        f"database {live_database_path()}: that repo is in a temp directory, which is a test's "
        "or a probe's. Point the process at its own database (LOREGARDEN_DATABASE_URL, the "
        "`isolated_db` fixture, or `task sandbox`)."
    )


@event.listens_for(Workspace, "before_insert")
def _guard_insert(_mapper: Mapper, connection: Connection, target: Workspace) -> None:
    _check(target, connection)


@event.listens_for(Workspace, "before_update")
def _guard_update(_mapper: Mapper, connection: Connection, target: Workspace) -> None:
    if inspect(target).attrs["repo_path"].history.has_changes():
        _check(target, connection)
