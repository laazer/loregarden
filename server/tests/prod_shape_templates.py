"""Build the production-shaped scenario once per module, and copy it per test.

`build_prod_shape` costs ~7s, and the files that test it built it in every
test's setup: about 130s of the CI server suite between `test_prod_shape.py`
and `test_prod_shape_fixtures.py`. A template holds the built database and the
memory-graph files beside it; each test copies both into its own database and
memory store, the same way `tests/db_templates.py` copies the schema.

Module scope rather than session scope, on purpose. The scenario's activity
events and merge-queue entries are stamped relative to the build's
``utcnow()``, and the tests read them against a "since" window measured from
the test's own clock. A session template could be built many minutes before a
test that reads it; a module template is read within that file's own run, so
the drift is seconds, as it was within one slow test before.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loregarden.services.memory_scope import graph_path_for_workspace
from loregarden.services.memory_store import AgentMemoryService
from loregarden.testing.prod_shape import CALIBRATION, ProdShapeSummary, build_prod_shape
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine
from tests.db_templates import copy_database


@dataclass(frozen=True)
class ProdShapeTemplate:
    """The scenario built once: its database, its memory graphs, and its summary."""

    database: Path
    #: Workspace slug -> that workspace's graph file, as `build_prod_shape` wrote it.
    graphs: dict[str, Path]
    summary: ProdShapeSummary


def build_prod_shape_template(root: Path, source: Path) -> ProdShapeTemplate:
    """Build the scenario on a copy of ``source`` (a template database) under ``root``.

    The graphs go where `AgentMemoryService` would put them under a memory base
    of ``root/memory/memory.db``, so a test's copy lands at the same relative
    place under its own base.
    """
    database = root / "prod-shape.db"
    copy_database(source, database)
    base = root / "memory" / "memory.db"

    def graph_path(slug: str) -> Path | None:
        return graph_path_for_workspace(base, slug)

    engine = create_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            summary = build_prod_shape(session, graph_path=graph_path)
    finally:
        engine.dispose()
    graphs = {slug: path for slug in CALIBRATION.memory if (path := graph_path(slug))}
    return ProdShapeTemplate(database=database, graphs=graphs, summary=summary)


def restore_prod_shape(template: ProdShapeTemplate, engine: Engine, *, graphs: bool) -> None:
    """Copy the template into ``engine``'s database and, when asked, its graphs
    into this test's memory store (`isolated_memory_store` points it per test)."""
    engine.dispose()
    copy_database(template.database, Path(engine.url.database))
    # Outside any transaction, where SQLite honours it (as `isolated_db` sets it).
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    if not graphs:
        return
    service = AgentMemoryService.from_settings()
    for slug, source in template.graphs.items():
        destination = service.graph_path(slug)
        assert destination is not None, "the test's memory store names no graph base"
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_database(source, destination)
