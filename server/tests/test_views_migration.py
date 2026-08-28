"""Migration 0082 creates the view store.

Two tables land together because one feature drives both, and because the
ordering they share is the reason they cannot be added separately: a view's
position in the sidebar lives on ``sidebar_entries``, never on ``views``.

Applied twice where the point is idempotence — a migration that guards its own
changes is the only kind safe to re-run, and re-running is what happens when a
branch that already migrated is merged forward.
"""

import tempfile

import loregarden.models.domain  # noqa: F401  (registers the tables on SQLModel.metadata)
import pytest
from loregarden.db import migrations as M
from loregarden.db.migration_ids import SHIPPED_MIGRATION_IDS
from loregarden.db.migrations_views import (
    m_sidebar_entry_pinned,
    m_view_store,
    m_view_viewport,
)
from loregarden.models.domain import Workspace
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

MIGRATION_ID = "0087_view_store"


def _fresh_engine():
    tmp = tempfile.mkdtemp()
    return create_engine(f"sqlite:///{tmp}/t.db")


def _columns(engine, table: str) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _orm_engine(table: str):
    """A database built the way a fresh install builds it — ``create_all``."""
    engine = _fresh_engine()
    SQLModel.metadata.create_all(engine, tables=[SQLModel.metadata.tables[table]])
    return engine


def _affinity(type_name: str) -> str:
    """Collapse the spellings SQLite treats as one storage class.

    ``create_all`` emits ``VARCHAR``/``VARCHAR(9)``/``DATETIME`` where a
    hand-written migration says ``TEXT``. SQLite has no such types — all four
    are the TEXT affinity, and every migration in this repo is written in the
    plain spelling. Comparing the words would fail on a difference that does
    not exist in the stored schema, so normalize the spelling and keep
    comparing everything else exactly.
    """
    upper = type_name.upper()
    if upper.startswith(("VARCHAR", "TEXT", "CHAR", "CLOB", "DATETIME", "DATE", "TIME")):
        return "TEXT"
    return upper


def _schema(engine, table: str) -> dict:
    """Everything about ``table`` the two builders must agree on."""
    inspector = inspect(engine)
    primary_key = tuple(inspector.get_pk_constraint(table)["constrained_columns"])

    columns = {}
    for column in inspector.get_columns(table):
        # ``id TEXT PRIMARY KEY`` in SQLite is nullable — the NOT NULL that a
        # rowid alias would imply is not applied to a TEXT key, so PRAGMA
        # reports notnull=0 while ``create_all`` spells NOT NULL out. Every
        # hand-written migration here carries the same gap; it is a quirk of
        # the DDL dialect, not a schema difference. Normalize the primary key
        # to NOT NULL on both sides so real nullability drift still fails.
        nullable = False if column["name"] in primary_key else column["nullable"]
        columns[column["name"]] = (_affinity(str(column["type"])), nullable)

    return {
        "columns": columns,
        "primary_key": primary_key,
        "indexes": {
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table)
        },
        # Column tuples, not names: an inline ``UNIQUE (a, b)`` in a migration is
        # anonymous, while ``create_all`` names the constraint. The constraint
        # enforced is identical; only the label differs.
        "unique_constraints": {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table)
        },
        # Compared by expression rather than by name, for the reason above: an
        # inline ``CHECK`` in a migration is anonymous while ``create_all`` names
        # it, and the rule enforced is the expression.
        "check_constraints": {
            constraint["sqltext"] for constraint in inspector.get_check_constraints(table)
        },
        "foreign_keys": {
            (
                tuple(key["constrained_columns"]),
                key["referred_table"],
                tuple(key["referred_columns"]),
            )
            for key in inspector.get_foreign_keys(table)
        },
    }


def _apply(engine, times: int = 1) -> None:
    """Every migration these two tables own, in ledger order.

    ``m_sidebar_entry_pinned`` (472) and ``m_view_viewport`` (480) are separate
    ids because ``m_view_store`` already shipped and is append-only, but all
    three describe these two tables — so the ORM-parity check has to see every
    one of them, or it compares the ORM against a schema no deployment ever has.
    """
    for _ in range(times):
        with engine.begin() as conn:
            m_view_store(conn)
            m_sidebar_entry_pinned(conn)
            m_view_viewport(conn)


def _seed_parents(engine, workspaces: tuple[str, ...] = ("ws1",), views: tuple[str, ...] = ()):
    """The rows the entries under test point at, created for real.

    Both tables declare foreign keys, and those are enforced now
    (``db.session._enforce_foreign_keys`` sets ``PRAGMA foreign_keys=ON`` on
    every SQLite connection in the process). A row naming a workspace or a view
    that does not exist therefore fails on the reference — and against an engine
    holding only these two tables it fails on ``workspaces`` not existing at all
    — before the CHECK or the UNIQUE under test is ever evaluated. Seeding the
    parents is what makes the constraint under test the reason a row is refused.

    The workspace rows come from the mapped model rather than a stub table so the
    reference points at the real parent, and the view rows go in through the
    migration's own table.
    """
    # ``workflow_templates`` comes along because ``workspaces`` declares an
    # optional reference to it, and SQLite refuses an INSERT into a table whose
    # parent table is missing even when the referencing column is NULL.
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SQLModel.metadata.tables["workflow_templates"],
            SQLModel.metadata.tables["workspaces"],
        ],
    )
    with Session(engine) as session:
        for workspace_id in workspaces:
            session.add(Workspace(id=workspace_id, slug=workspace_id, name=workspace_id))
        session.commit()

    owner = workspaces[0]
    with engine.begin() as conn:
        for view_id in views:
            conn.execute(
                text(
                    "INSERT INTO views "
                    "(id, workspace_id, kind, title, icon, layout_json, "
                    "created_at, updated_at) "
                    f"VALUES ('{view_id}', '{owner}', 'flex_grid', 'Board', '', '{{}}', "
                    "'2026-01-01T00:00:00', '2026-01-01T00:00:00')"
                )
            )


def test_migration_creates_the_views_table():
    engine = _fresh_engine()
    _apply(engine)

    assert {
        "id",
        "workspace_id",
        "kind",
        "title",
        "icon",
        "layout_json",
        "created_at",
        "updated_at",
    } <= _columns(engine, "views")


def test_the_views_table_carries_its_constraints():
    """Stated absolutely, not only against the model.

    The drift test below compares the migration with ``create_all``, which by
    construction cannot see a constraint dropped from both sides at once — the
    two agree on the weaker schema. So the constraints this table is *for* are
    named here as well.
    """
    engine = _fresh_engine()
    _apply(engine)
    schema = _schema(engine, "views")

    assert schema["primary_key"] == ("id",)
    # Nothing on a view is optional: a missing title or layout is a view that
    # renders as a blank, not a view with less in it.
    assert [name for name, (_, nullable) in schema["columns"].items() if nullable] == []
    assert schema["foreign_keys"] == {(("workspace_id",), "workspaces", ("id",))}
    assert schema["indexes"] == {("ix_views_workspace_id", ("workspace_id",), False)}


def test_migration_creates_the_sidebar_entries_table():
    """Ordering lives here and only here, for views and pinned pages alike."""
    engine = _fresh_engine()
    _apply(engine)

    assert {
        "id",
        "workspace_id",
        "position",
        "entry_kind",
        "page_key",
        "view_id",
    } <= _columns(engine, "sidebar_entries")


def test_the_sidebar_entries_table_carries_its_constraints():
    """The rules this table exists to enforce, asserted absolutely.

    One entry per rank, one entry per pinned page, one entry per view, and an
    entry that is a page or a view rather than both or neither. Each is a
    constraint rather than a convention precisely so no write path can forget it
    — which makes silently losing one the failure worth a test of its own.
    """
    engine = _fresh_engine()
    _apply(engine)
    schema = _schema(engine, "sidebar_entries")

    assert schema["primary_key"] == ("id",)
    # Exactly the two halves of the payload are optional. Both nullable is what
    # lets each half's UNIQUE ignore entries of the other kind instead of
    # colliding all of them on one blank value.
    assert {name for name, (_, nullable) in schema["columns"].items() if nullable} == {
        "page_key",
        "view_id",
    }
    assert schema["foreign_keys"] == {
        (("workspace_id",), "workspaces", ("id",)),
        (("view_id",), "views", ("id",)),
    }
    assert schema["unique_constraints"] == {
        ("workspace_id", "page_key"),
        ("workspace_id", "position"),
        ("workspace_id", "view_id"),
    }
    assert schema["check_constraints"] == {"(page_key IS NULL) <> (view_id IS NULL)"}
    assert schema["indexes"] == {("ix_sidebar_entries_workspace_id", ("workspace_id",), False)}


@pytest.mark.parametrize(
    ("page_key", "view_id"),
    [
        pytest.param("NULL", "NULL", id="neither-half-set"),
        pytest.param("'tickets'", "'v1'", id="both-halves-set"),
    ],
)
def test_an_entry_must_hold_exactly_one_half(page_key: str, view_id: str):
    """Asserted against the database rather than the service.

    An entry with neither half set renders as nothing and can never be resolved
    to anything; one with both set is a row whose two columns disagree about what
    kind of entry it is, and every reader picks a different winner.

    Both rows name a workspace and a view that exist, so the only thing wrong
    with either is the half it sets — asserted on the message, because a row
    refused by a foreign key raises the same exception class and would pass this
    test while proving the CHECK is gone.
    """
    engine = _fresh_engine()
    _apply(engine)
    _seed_parents(engine, views=("v1",))

    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO sidebar_entries "
                    "(id, workspace_id, position, entry_kind, page_key, view_id) "
                    f"VALUES ('e1', 'ws1', 0, 'view', {page_key}, {view_id})"
                )
            )


def test_one_view_cannot_be_ranked_twice():
    """A second entry for one view would list it twice and give it two places in
    an ordering that is supposed to be total.

    The second entry differs from the first only in id and rank, so the UNIQUE on
    ``(workspace_id, view_id)`` is the one thing left to refuse it — matched on
    the message, since a missing view would raise the same class.
    """
    engine = _fresh_engine()
    _apply(engine)
    _seed_parents(engine, views=("v1",))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sidebar_entries "
                "(id, workspace_id, position, entry_kind, page_key, view_id) "
                "VALUES ('e1', 'ws1', 0, 'view', NULL, 'v1')"
            )
        )

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO sidebar_entries "
                    "(id, workspace_id, position, entry_kind, page_key, view_id) "
                    "VALUES ('e2', 'ws1', 1, 'view', NULL, 'v1')"
                )
            )


def test_two_workspaces_may_each_rank_a_view():
    """The UNIQUE is per workspace, not global — and view ids being unique across
    workspaces is what would hide a global constraint here as a passing test.

    One view id in two workspaces' entries is the only shape that can tell a
    composite key from a global one, so the second workspace's entry names the
    first workspace's view. The reference is real; what it crosses is the
    workspace, which is exactly the column the constraint is keyed on.
    """
    engine = _fresh_engine()
    _apply(engine)
    _seed_parents(engine, workspaces=("ws1", "ws2"), views=("v1",))

    with engine.begin() as conn:
        for index, workspace in enumerate(("ws1", "ws2")):
            conn.execute(
                text(
                    "INSERT INTO sidebar_entries "
                    "(id, workspace_id, position, entry_kind, page_key, view_id) "
                    f"VALUES ('e{index}', '{workspace}', 0, 'view', NULL, 'v1')"
                )
            )

    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM sidebar_entries")).scalar() == 2


def test_a_second_run_changes_nothing():
    engine = _fresh_engine()
    _apply(engine)
    first = (_columns(engine, "views"), _columns(engine, "sidebar_entries"))

    _apply(engine)

    assert (_columns(engine, "views"), _columns(engine, "sidebar_entries")) == first


def test_migration_does_not_discard_existing_rows():
    """The second run must guard, not recreate — a DROP would take the data."""
    engine = _fresh_engine()
    _apply(engine)
    _seed_parents(engine, views=("v1",))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sidebar_entries "
                "(id, workspace_id, position, entry_kind, page_key, view_id) "
                "VALUES ('e1', 'ws1', 0, 'view', NULL, 'v1')"
            )
        )

    _apply(engine)

    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM views")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM sidebar_entries")).scalar() == 1


@pytest.mark.parametrize("table", ["views", "sidebar_entries"])
def test_the_migration_and_the_orm_describe_the_same_table(table: str):
    """The two column sets above are subset assertions, and a subset is all they
    can be — neither knows the whole column list.

    Together they let the migration and the mapped model drift: ``session.py``
    runs ``create_all`` before ``apply_migrations``, so on a fresh database the
    model wins and a migration describing a different table is never exercised.
    The migration is then fiction, and the one database it does run against
    first — an existing deployment — gets the wrong schema. Pin them to each
    other; there is only one right answer for what these tables are.

    Column names alone are not that answer: a foreign key, a UNIQUE, a CHECK, an
    index, or a NOT NULL can be dropped from one side with every name matching.
    Both databases are therefore built for real and compared through the
    inspector, so what is asserted is the schema SQLite ended up with rather
    than the DDL text either side was written in.
    """
    migrated = _fresh_engine()
    _apply(migrated)

    assert _schema(migrated, table) == _schema(_orm_engine(table), table)


def test_migration_is_registered_and_appended_to_the_ledger():
    """``test_migration_ids`` already compares the two lists to each other; this
    is the half it cannot know — which id this ticket adds."""
    ids = [migration_id for migration_id, _ in M.MIGRATIONS]

    assert MIGRATION_ID in ids
    assert MIGRATION_ID in SHIPPED_MIGRATION_IDS
    # The id must run *this* body — a ledger entry pointing at some other
    # callable satisfies every string assertion and migrates nothing.
    assert dict(M.MIGRATIONS)[MIGRATION_ID] is m_view_store
    # Appended, not inserted: it lands after everything that shipped before it.
    assert SHIPPED_MIGRATION_IDS.index(MIGRATION_ID) > SHIPPED_MIGRATION_IDS.index(
        "0081_agent_run_preflight"
    )


PINNED_MIGRATION_ID = "0091_sidebar_entry_pinned"


def test_the_pinned_migration_is_registered_and_appended_to_the_ledger():
    """The same two facts for 472's column, which ships as its own id.

    ``m_view_store`` guards its own CREATE TABLE, so on a database that already
    has ``sidebar_entries`` it does nothing at all — which is every database
    that shipped 434. The column therefore cannot be folded into it.
    """
    ids = [migration_id for migration_id, _ in M.MIGRATIONS]

    assert PINNED_MIGRATION_ID in ids
    assert PINNED_MIGRATION_ID in SHIPPED_MIGRATION_IDS
    assert dict(M.MIGRATIONS)[PINNED_MIGRATION_ID] is m_sidebar_entry_pinned
    assert SHIPPED_MIGRATION_IDS.index(PINNED_MIGRATION_ID) > SHIPPED_MIGRATION_IDS.index(
        MIGRATION_ID
    )


def test_an_existing_entry_is_unpinned_after_the_column_lands():
    """The backfill every workspace 434 seeded depends on.

    Those rows predate pinned views entirely, so the only defensible answer is
    Tabs. A column added without a default would make them NULL, and a NULL
    read as neither pinned nor unpinned draws the tab in no section at all.
    """
    engine = _fresh_engine()
    with engine.begin() as conn:
        m_view_store(conn)
    _seed_parents(engine, views=("v1",))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sidebar_entries (id, workspace_id, position, entry_kind, view_id) "
                "VALUES ('e1', 'ws1', 10, 'view', 'v1')"
            )
        )

    with engine.begin() as conn:
        m_sidebar_entry_pinned(conn)

    with engine.begin() as conn:
        pinned = conn.execute(text("SELECT pinned FROM sidebar_entries WHERE id = 'e1'")).scalar()
    assert pinned == 0


def test_the_pinned_migration_is_idempotent():
    """It runs on every boot of every database, including ones already carrying
    the column."""
    engine = _fresh_engine()
    _apply(engine, times=3)

    assert "pinned" in _columns(engine, "sidebar_entries")


VIEWPORT_MIGRATION_ID = "0098_view_viewport"


def test_the_viewport_migration_is_registered_and_appended_to_the_ledger():
    """The same two facts for 480's column, which ships as its own id.

    ``m_view_store`` guards its own CREATE TABLE, so on every database that
    already holds ``views`` it does nothing — the column cannot be folded into
    it. The number skips 0096, which a parallel branch has already applied to
    the live database; claiming it again would run two different bodies under
    one id depending on which branch a deployment saw first.
    """
    ids = [migration_id for migration_id, _ in M.MIGRATIONS]

    assert VIEWPORT_MIGRATION_ID in ids
    assert VIEWPORT_MIGRATION_ID in SHIPPED_MIGRATION_IDS
    assert dict(M.MIGRATIONS)[VIEWPORT_MIGRATION_ID] is m_view_viewport
    assert SHIPPED_MIGRATION_IDS.index(VIEWPORT_MIGRATION_ID) > SHIPPED_MIGRATION_IDS.index(
        PINNED_MIGRATION_ID
    )


def test_the_views_table_carries_the_viewport_column():
    engine = _fresh_engine()
    _apply(engine)

    assert "viewport_json" in _columns(engine, "views")


def test_an_existing_view_has_no_stored_position_after_the_column_lands():
    """The backfill every view composed before 480 depends on.

    ``'{}'`` is the absent viewport — no stored position, so the canvas opens at
    its default. A column added without a default would make those rows NULL,
    and a NULL is a second spelling of the same state that every reader would
    then have to know about.
    """
    engine = _fresh_engine()
    with engine.begin() as conn:
        m_view_store(conn)
    _seed_parents(engine, views=("v1",))

    with engine.begin() as conn:
        m_view_viewport(conn)

    with engine.begin() as conn:
        stored = conn.execute(text("SELECT viewport_json FROM views WHERE id = 'v1'")).scalar()
    assert stored == "{}"


def test_the_viewport_migration_is_idempotent():
    """It runs on every boot of every database, including ones already carrying
    the column."""
    engine = _fresh_engine()
    _apply(engine, times=3)

    assert "viewport_json" in _columns(engine, "views")
