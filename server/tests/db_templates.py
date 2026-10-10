"""Build each test's starting database once per process, and copy it per test.

Two templates, for the two fixtures that used to build from scratch every time.

**The schema**, for ``isolated_db``, which every test uses: ``create_all`` cost
~0.15s per test, ~16 CPU-minutes a run, to produce the same empty schema six
thousand times.

**The seeded database and workspace repo**, for tests that request ``client``.
That fixture used to seed, ``git init`` a throwaway repo (five subprocesses),
then start the app, whose lifespan re-ran every migration against a schema with
no ledger (so every guard ran) and seeded a second time — about 3s per test
before the test ran a line, across roughly 780 tests.

Both are copied by ``isolated_db`` before its engine opens. That placement is
the point: a test's own fixtures write into ``isolated_db`` before ``client``
is set up (the docker-capacity tests pin a ceiling that way), so restoring any
later would silently erase them.

The SQLite backup API rather than a file copy: it is consistent even if a
source was left in WAL mode with frames not yet checkpointed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import SQLModel, create_engine
from tests.repo_templates import copy_repo


@dataclass(frozen=True)
class SeededTemplate:
    """A seeded, migrated database whose workspace points at ``repo``."""

    database: Path
    repo: Path

    def copy_repo(self, destination: Path) -> Path:
        """A private copy of the workspace repo for one test."""
        copy_repo(self.repo, destination)
        return destination


def build_schema_template(path: Path) -> Path:
    """Write an empty database holding every table in ``SQLModel.metadata``.

    Built after collection, so every model module the suite imports has
    registered its tables. Nothing in the suite defines a table of its own.
    """
    engine = create_engine(f"sqlite:///{path}")
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()
    return path


def copy_database(source: Path, destination: Path) -> None:
    """Copy one SQLite database to a path no engine has opened yet."""
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
