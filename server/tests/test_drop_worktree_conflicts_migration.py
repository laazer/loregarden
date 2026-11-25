"""The first migration that removes anything, and the table an older build brings back."""

from __future__ import annotations

import logging

import pytest
from loregarden.db.migration_utils import table_columns, table_exists
from loregarden.db.retired_tables import drop_retired_tables
from loregarden.db.versions.drop_worktree_conflicts import m_drop_worktree_conflicts
from loregarden.models.domain import Worktree
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace

DROPPED = {"has_conflicts", "conflict_files_json", "conflict_summary", "merged_at"}
REPORTS = (
    "CREATE TABLE conflict_reports (id VARCHAR PRIMARY KEY, "
    "worktree_id VARCHAR NOT NULL REFERENCES worktrees (id))"
)


@pytest.fixture(name="old_schema")
def old_schema_fixture(isolated_db):
    """The live schema before this migration, holding one worktree."""
    with Session(isolated_db) as session:
        make_workspace(session, workspace_id="ws", slug="ws")
        make_agent_run(session, workspace_id="ws", run_id="run")
        session.add(Worktree(id="wt", workspace_id="ws", agent_run_id="run", branch="b"))
        session.commit()
    with isolated_db.begin() as conn:
        for ddl in (
            "has_conflicts BOOLEAN NOT NULL DEFAULT 0",
            "conflict_files_json VARCHAR NOT NULL DEFAULT '[]'",
            "conflict_summary VARCHAR",
            "merged_at DATETIME",
        ):
            conn.execute(text(f"ALTER TABLE worktrees ADD COLUMN {ddl}"))
        conn.execute(text(REPORTS))
        conn.execute(text("CREATE INDEX ix_conflict_reports_worktree_id ON conflict_reports (id)"))
    return isolated_db


def _indexes(conn) -> set[str]:
    rows = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'worktrees'")
    )
    return {row[0] for row in rows}


def test_the_columns_and_table_go_and_the_rows_and_indexes_stay(old_schema):
    with old_schema.begin() as conn:
        before, indexes = table_columns(conn, "worktrees"), _indexes(conn)
        m_drop_worktree_conflicts(conn)
        assert table_columns(conn, "worktrees") == before - DROPPED
        assert _indexes(conn) == indexes
        assert not table_exists(conn, "conflict_reports")

    with Session(old_schema) as session:
        assert session.get_one(Worktree, "wt").branch == "b"
        session.add(Worktree(id="new", workspace_id="ws", agent_run_id="run"))
        session.commit()


def test_a_replay_and_a_schema_without_worktrees_are_no_ops(old_schema):
    with old_schema.begin() as conn:
        m_drop_worktree_conflicts(conn)
        after = table_columns(conn, "worktrees")
        m_drop_worktree_conflicts(conn)
        assert table_columns(conn, "worktrees") == after
        conn.execute(text("DROP TABLE worktrees"))
        m_drop_worktree_conflicts(conn)


def test_an_empty_table_an_older_build_recreated_is_dropped(old_schema):
    assert drop_retired_tables(old_schema) == ["conflict_reports"]
    assert drop_retired_tables(old_schema) == []


def test_a_recreated_table_with_rows_is_kept_and_reported(old_schema, caplog):
    with old_schema.begin() as conn:
        conn.execute(text("INSERT INTO conflict_reports VALUES ('r', 'wt')"))

    with caplog.at_level(logging.WARNING):
        assert drop_retired_tables(old_schema) == []

    with old_schema.connect() as conn:
        assert table_exists(conn, "conflict_reports")
    assert "conflict_reports" in caplog.text


def test_a_locked_database_does_not_stop_the_boot(old_schema, caplog):
    def locked(_conn, _cursor, statement, *_rest):
        if statement.startswith("DROP TABLE"):
            raise OperationalError(statement, {}, Exception("database is locked"))

    event.listen(old_schema, "before_cursor_execute", locked)
    with caplog.at_level(logging.WARNING):
        assert drop_retired_tables(old_schema) == []
    assert "conflict_reports" in caplog.text


def test_the_server_drops_it_at_boot(client, isolated_db):
    with isolated_db.begin() as conn:
        conn.execute(text(REPORTS))

    with client:
        pass

    with isolated_db.connect() as conn:
        assert not table_exists(conn, "conflict_reports")
