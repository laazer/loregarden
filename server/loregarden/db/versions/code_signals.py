"""The behavioural git signal tables (lg-code-knowledge-299).

See `models.domain.code_signal_tables`. Both tables start empty — they are a
derived index, rebuilt wholesale by `services.code_signals.store`, so there is
nothing to backfill. Each statement guards itself, so running this twice is a
no-op and a partial build with only some of the indexes is completed rather
than failed.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_INDEXES = {
    "uq_file_git_signals_path": (
        "CREATE UNIQUE INDEX uq_file_git_signals_path ON file_git_signals (workspace_id, file_path)"
    ),
    "ix_file_git_signals_churn": (
        "CREATE INDEX ix_file_git_signals_churn ON file_git_signals (workspace_id, churn_commits)"
    ),
    "ix_file_git_signals_bugfix": (
        "CREATE INDEX ix_file_git_signals_bugfix ON file_git_signals (workspace_id, bugfix_commits)"
    ),
    "uq_file_co_change": (
        "CREATE UNIQUE INDEX uq_file_co_change "
        "ON file_co_change_pairs (workspace_id, file_path, partner_path)"
    ),
    "ix_file_co_change_support": (
        "CREATE INDEX ix_file_co_change_support "
        "ON file_co_change_pairs (workspace_id, file_path, support)"
    ),
}


@migration("20261116_code_signals", after="20261115_integration_branch_moves")
def m_code_signals(conn: Connection) -> None:
    if not table_exists(conn, "file_git_signals"):
        conn.execute(
            text(
                "CREATE TABLE file_git_signals ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "workspace_id VARCHAR NOT NULL REFERENCES workspaces(id), "
                "file_path VARCHAR NOT NULL, "
                "churn_commits INTEGER NOT NULL DEFAULT 0, "
                "churn_added INTEGER NOT NULL DEFAULT 0, "
                "churn_deleted INTEGER NOT NULL DEFAULT 0, "
                "last_commit_at DATETIME NOT NULL, "
                "top_author VARCHAR NOT NULL DEFAULT '', "
                "top_author_added_share FLOAT NOT NULL DEFAULT 0.0, "
                "distinct_authors INTEGER NOT NULL DEFAULT 0, "
                "bugfix_commits INTEGER NOT NULL DEFAULT 0, "
                "last_bugfix_at DATETIME, "
                "window_days INTEGER NOT NULL, "
                "head_sha VARCHAR NOT NULL DEFAULT '', "
                "computed_at DATETIME NOT NULL)"
            )
        )
    if not table_exists(conn, "file_co_change_pairs"):
        conn.execute(
            text(
                "CREATE TABLE file_co_change_pairs ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "workspace_id VARCHAR NOT NULL REFERENCES workspaces(id), "
                "file_path VARCHAR NOT NULL, "
                "partner_path VARCHAR NOT NULL, "
                "support INTEGER NOT NULL DEFAULT 0, "
                "computed_at DATETIME NOT NULL)"
            )
        )
    for name, ddl in _INDEXES.items():
        if not index_exists(conn, name):
            conn.execute(text(ddl))
