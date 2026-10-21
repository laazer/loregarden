"""A fork in the migration chain is allowed when its siblings commute.

Two branches written at once both follow the tip they saw. The suite used to
fail on that fork, and the second branch to merge re-pointed its `after` at the
first: on 2026-10-21 one PR did it twice in a few hours (#663, then #667), with
25 migration commits on main since 2026-10-01. That tested the wrong thing.
What a fork risks is narrower: a fresh database applies the siblings in id
order, the live one applied them in merge order, and the two disagree only when
the siblings interact.

So this applies the versioned migrations to a seeded, production-shaped
database twice, with every fork's siblings in id order and then reversed, and
compares every table. Values that differ between two runs in the *same* order
(timestamps a migration stamps) are not the order's doing and are left out.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from loregarden.db import migrations as M
from loregarden.db.migration_registry import SiblingOrder
from loregarden.db.migrations_runner import apply_pending
from loregarden.services.memory_store import AgentMemoryService
from loregarden.testing.prod_shape import build_prod_shape
from sqlalchemy import Connection, bindparam, create_engine, text
from sqlmodel import Session
from tests.db_templates import copy_database

Migrations = Sequence[tuple[str, Callable[[Connection], None]]]
Row = tuple[tuple[str, object], ...]
#: table -> its schema and its rows, each row as (column, value) pairs.
Snapshot = dict[str, tuple[str, list[Row]]]


def _snapshot(database: Path) -> Snapshot:
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"
                )
            ).all()
            found: Snapshot = {}
            for name, sql in tables:
                result = conn.execute(text(f'SELECT * FROM "{name}"'))
                columns = list(result.keys())
                rows = [tuple(zip(columns, row, strict=True)) for row in result.all()]
                found[name] = (sql, sorted(rows, key=repr))
            return found
    finally:
        engine.dispose()


def _migrated(base: Path, out: Path, migrations: Migrations, forget: Sequence[str]) -> Snapshot:
    """``base`` copied to ``out``, the ``forget`` ids re-run in ``migrations`` order."""
    copy_database(base, out)
    engine = create_engine(f"sqlite:///{out}")
    try:
        if forget:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM schema_migrations WHERE id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": list(forget)},
                )
        apply_pending(engine, list(migrations))
    finally:
        engine.dispose()
    return _snapshot(out)


def _unstable(one: Snapshot, other: Snapshot) -> set[tuple[str, str]]:
    """(table, column) pairs whose values differ between two identical runs."""
    unstable: set[tuple[str, str]] = set()
    for table, (_, rows) in one.items():
        twins = other.get(table, ("", []))[1]
        for row, twin in zip(rows, twins, strict=False):
            unstable |= {
                (table, column) for (column, a), (_, b) in zip(row, twin, strict=True) if a != b
            }
    return unstable


def _without(snapshot: Snapshot, unstable: set[tuple[str, str]]) -> Snapshot:
    return {
        table: (
            sql,
            sorted(
                (tuple((c, v) for c, v in row if (table, c) not in unstable) for row in rows),
                key=repr,
            ),
        )
        for table, (sql, rows) in snapshot.items()
    }


def order_dependent_tables(
    base: Path,
    work: Path,
    by_id: Migrations,
    reversed_: Migrations,
    forget: Sequence[str] = (),
) -> list[str]:
    """Tables that end up different when the forks run the other way round."""
    work.mkdir(parents=True, exist_ok=True)
    first = _migrated(base, work / "by_id.db", by_id, forget)
    again = _migrated(base, work / "by_id_again.db", by_id, forget)
    other = _migrated(base, work / "reversed.db", reversed_, forget)
    unstable = _unstable(first, again)
    first, other = _without(first, unstable), _without(other, unstable)
    return sorted(
        table for table in first.keys() | other.keys() if first.get(table) != other.get(table)
    )


# ---- the real chain --------------------------------------------------------


@pytest.fixture(name="production_shaped")
def production_shaped_fixture(client, isolated_db) -> Path:
    """The seeded database (templates, agents, workflows) with production's
    proportions of tickets on top: what data migrations actually rewrite."""
    with Session(isolated_db) as session:
        build_prod_shape(session, graph_path=AgentMemoryService.from_settings().graph_path)
    isolated_db.dispose()
    return Path(isolated_db.url.database)


def test_migration_forks_commute(production_shaped: Path, tmp_path: Path):
    by_id = M.ordered_migrations(SiblingOrder.BY_ID)
    reversed_ = M.ordered_migrations(SiblingOrder.REVERSED)
    if not M.VERSION_CHAIN.forks:
        assert by_id == reversed_, "no fork, so there is only one order"
    versioned = [item.id for item in M.VERSION_CHAIN.ordered]

    differ = order_dependent_tables(production_shaped, tmp_path, by_id, reversed_, versioned)

    forks = "; ".join(
        f"{', '.join(fork.children)} follow {fork.parent}" for fork in M.VERSION_CHAIN.forks
    )
    assert not differ, (
        f"These migrations do not commute ({forks}): applied the other way round they "
        f"leave {', '.join(differ)} different. One depends on the other, so make it say so: "
        "point its `after` at the one it needs. Do not rename either id."
    )


# ---- the check catches what it claims to -----------------------------------


@pytest.fixture(name="base")
def base_fixture(tmp_path: Path) -> Path:
    database = tmp_path / "base.db"
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (k INTEGER PRIMARY KEY, x INTEGER, stamped TEXT)"))
        conn.execute(text("INSERT INTO t (k, x) VALUES (1, 3)"))
    engine.dispose()
    return database


def _sql(statement: str) -> Callable[[Connection], None]:
    def migrate(conn: Connection) -> None:
        conn.execute(text(statement))

    return migrate


def test_siblings_that_interact_are_caught(base: Path, tmp_path: Path):
    set_x = ("20261021_set_x", _sql("UPDATE t SET x = 1"))
    double_x = ("20261021_double_x", _sql("UPDATE t SET x = x * 2"))

    assert order_dependent_tables(base, tmp_path, [set_x, double_x], [double_x, set_x]) == ["t"]


def test_siblings_that_commute_pass(base: Path, tmp_path: Path):
    add_column = ("20261021_add_y", _sql("ALTER TABLE t ADD COLUMN y INTEGER"))
    bump_x = ("20261021_bump_x", _sql("UPDATE t SET x = x + 1"))

    assert order_dependent_tables(base, tmp_path, [add_column, bump_x], [bump_x, add_column]) == []


def test_a_timestamp_alone_is_not_an_order_dependence(base: Path, tmp_path: Path):
    """Two runs in one order differ in a stamp too; that is not the order's doing.
    Random rather than a clock, so the two runs cannot land on the same tick."""
    stamp = ("20261021_stamp", _sql("UPDATE t SET stamped = hex(randomblob(8))"))
    bump_x = ("20261021_bump_x", _sql("UPDATE t SET x = x + 1"))

    assert order_dependent_tables(base, tmp_path, [stamp, bump_x], [bump_x, stamp]) == []
